#! /usr/bin/python3

import gi
gi.require_version('CScreensaver', '1.0')

from gi.repository import Gtk, Gdk, GObject, CScreensaver

import utils
import trackers
import settings
import status
import constants as c
from fader import Fader
from eventHandler import EventHandler
from monitorView import MonitorView
from unlock import UnlockDialog
from clock import ClockWidget
from audioBar import AudioBar
from infoBar import InfoBar

class Stage(Gtk.Window):
    def __init__(self, screen, manager, away_message):
        Gtk.Window.__init__(self,
                            type=Gtk.WindowType.POPUP,
                            decorated=False,
                            skip_taskbar_hint=True,
                            skip_pager_hint=True)

        trackers.con_tracker_get().connect(settings.bg,
                                           "changed", 
                                           self.on_bg_changed)

        self.destroying = False

        self.manager = manager
        self.screen = screen
        self.away_message = away_message

        self.monitors = []
        self.last_focus_monitor = -1
        self.overlay = None
        self.clock_widget = None
        self.unlock_dialog = None
        self.status_bar = None

        self.event_handler = EventHandler(manager)

        self.get_style_context().remove_class("background")

        self.set_events(self.get_events() |
                        Gdk.EventMask.POINTER_MOTION_MASK |
                        Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.BUTTON_RELEASE_MASK |
                        Gdk.EventMask.KEY_PRESS_MASK |
                        Gdk.EventMask.KEY_RELEASE_MASK |
                        Gdk.EventMask.EXPOSURE_MASK |
                        Gdk.EventMask.VISIBILITY_NOTIFY_MASK |
                        Gdk.EventMask.ENTER_NOTIFY_MASK |
                        Gdk.EventMask.LEAVE_NOTIFY_MASK |
                        Gdk.EventMask.FOCUS_CHANGE_MASK)

        self.update_geometry()

        self.set_keep_above(True)
        self.fullscreen()

        self.overlay = Gtk.Overlay()
        self.fader = Fader(self)

        trackers.con_tracker_get().connect(self.overlay,
                                           "realize",
                                           self.on_realized)

        trackers.con_tracker_get().connect(self.overlay,
                                           "get-child-position",
                                           self.position_overlay_child)

        self.overlay.show_all()
        self.add(self.overlay)

        self.gdk_filter = CScreensaver.GdkEventFilter()

    def transition_in(self, effect_time, callback):
        self.show()
        self.fader.fade_in(effect_time, callback)

    def transition_out(self, effect_time, callback):
        if self.destroying:
            return

        self.destroying = True

        self.fader.cancel()

        self.fader.fade_out(effect_time, callback)

    def focus_and_present(self):
        utils.override_user_time(self.get_window())
        self.present()

    def on_realized(self, widget):
        window = self.get_window()

        window.set_fullscreen_mode(Gdk.FullscreenMode.ALL_MONITORS)
        window.move_resize(self.rect.x, self.rect.y, self.rect.width, self.rect.height)

        self.setup_children()

        self.gdk_filter.start(self)
        # self.focus_and_present()

    def setup_children(self):
        self.setup_monitors()
        self.setup_clock()
        self.setup_unlock()
        self.setup_status_bars()

    def destroy_stage(self):
        trackers.con_tracker_get().disconnect(settings.bg,
                                              "changed",
                                              self.on_bg_changed)

        self.set_timeout_active(None, False)

        for monitor in self.monitors:
            monitor.destroy()

        self.fader = None
        self.unlock_dialog = None
        self.clock_widget = None
        self.away_message = None
        self.monitors = []

        self.gdk_filter.stop()
        self.gdk_filter = None

        self.destroy()

    def setup_monitors(self):
        n = self.screen.get_n_monitors()

        for index in range(n):
            monitor = MonitorView(self.screen, index)

            image = Gtk.Image()

            settings.bg.create_and_set_gtk_image (image,
                                                  monitor.rect.width,
                                                  monitor.rect.height)

            monitor.set_initial_wallpaper_image(image)

            self.monitors.append(monitor)

            monitor.show_starting_view()
            monitor.reveal()

            self.add_child_widget(monitor)
            self.put_on_bottom(monitor)

            monitor.queue_draw()

    def on_bg_changed(self, bg):
        for monitor in self.monitors:
            image = Gtk.Image()

            settings.bg.create_and_set_gtk_image (image,
                                                  monitor.rect.width,
                                                  monitor.rect.height)

            monitor.set_next_wallpaper_image(image)

    def setup_clock(self):
        self.clock_widget = ClockWidget(self.screen, self.away_message, utils.get_mouse_monitor())
        self.add_child_widget(self.clock_widget)

        if settings.get_screensaver_name() == "":
            self.clock_widget.show_all()
            self.clock_widget.reveal()
            self.clock_widget.start_positioning()
            self.put_on_top(self.clock_widget)

    def setup_unlock(self):
        self.unlock_dialog = UnlockDialog()
        self.add_child_widget(self.unlock_dialog)
        self.put_on_bottom(self.unlock_dialog)

        # Prevent a dialog timeout during authentication
        trackers.con_tracker_get().connect(self.unlock_dialog,
                                           "inhibit-timeout",
                                           self.set_timeout_active, False)
        trackers.con_tracker_get().connect(self.unlock_dialog,
                                           "uninhibit-timeout",
                                           self.set_timeout_active, True)

        # Respond to authentication success/failure
        trackers.con_tracker_get().connect(self.unlock_dialog,
                                           "auth-success",
                                           self.authentication_result_callback, True)
        trackers.con_tracker_get().connect(self.unlock_dialog,
                                           "auth-failure",
                                           self.authentication_result_callback, False)

    def setup_status_bars(self):
        self.audio_bar = AudioBar(self.screen)
        self.add_child_widget(self.audio_bar)
        self.put_on_top(self.audio_bar)

        self.info_bar = InfoBar(self.screen)
        self.add_child_widget(self.info_bar)
        self.put_on_top(self.info_bar)

    def queue_dialog_key_event(self, event):
        self.unlock_dialog.queue_key_event(event)

# Timer stuff - after a certain time, the unlock dialog will cancel itself.
# This timer is suspended during authentication, and any time a new user event is received

    def reset_timeout(self):
        self.set_timeout_active(None, True)

    def set_timeout_active(self, dialog, active):
        if active:
            trackers.timer_tracker_get().start("wake-timeout",
                                               c.UNLOCK_TIMEOUT * 1000,
                                               self.on_wake_timeout)
        else:
            trackers.timer_tracker_get().cancel("wake-timeout")

    def on_wake_timeout(self):
        self.set_timeout_active(None, False)
        self.manager.cancel_unlock_widget()

        return False

    def authentication_result_callback(self, dialog, success):
        if success:
            self.clock_widget.hide()
            self.unlock_dialog.hide()
            self.manager.unlock()
        else:
            self.unlock_dialog.blink()

    def set_message(self, msg):
        self.clock_widget.set_message(msg)

# Methods that manipulate the unlock dialog

    def raise_unlock_widget(self):
        self.reset_timeout()

        if status.Awake:
            return

        self.clock_widget.stop_positioning()

        for monitor in self.monitors:
            monitor.show_wallpaper()

        #FIXME - wrong way to do this, it should start exactly after the stack animation
        #        completes in monitor.show_wallpaper(), however, sometimes we're
        #        already showing the wallpaper, if we're not using a plugin...
        GObject.timeout_add(260, self.after_wallpaper_shown_for_unlock)

    def after_wallpaper_shown_for_unlock(self):
        self.put_on_top(self.clock_widget)
        self.put_on_top(self.unlock_dialog)

        self.clock_widget.show()
        self.clock_widget.reveal()

        self.unlock_dialog.show()
        self.unlock_dialog.reveal()

        self.audio_bar.show()
        self.info_bar.show()
        self.audio_bar.reveal()
        self.info_bar.reveal()

        status.Awake = True

    def cancel_unlock_widget(self):
        if not status.Awake:
            return

        self.set_timeout_active(None, False)

        if settings.get_screensaver_name() != "":
            self.clock_widget.unreveal()
            self.clock_widget.hide()

        trackers.con_tracker_get().connect(self.unlock_dialog,
                                           "notify::child-revealed",
                                           self.after_unlock_unrevealed)
        self.unlock_dialog.unreveal()
        self.audio_bar.unreveal()
        self.info_bar.unreveal()

    def after_unlock_unrevealed(self, obj, pspec):
        self.unlock_dialog.hide()
        self.unlock_dialog.cancel()
        self.audio_bar.hide()
        self.info_bar.hide()

        trackers.con_tracker_get().disconnect(self.unlock_dialog,
                                              "notify::child-revealed",
                                              self.after_unlock_unrevealed)

        for monitor in self.monitors:
            monitor.show_plugin()

        status.Awake = False

        self.clock_widget.start_positioning()

    def do_motion_notify_event(self, event):
        return self.event_handler.on_motion_event(event)

    def do_key_press_event(self, event):
        return self.event_handler.on_key_press_event(event)

    def do_button_press_event(self, event):
        return self.event_handler.on_button_press_event(event)

    # Override BaseWindow.update_geometry
    def update_geometry(self):
        self.rect = Gdk.Rectangle()
        self.rect.x = 0
        self.rect.y = 0
        self.rect.width = self.screen.get_width()
        self.rect.height = self.screen.get_height()

# Overlay window management #

    def maybe_update_layout(self):
        current_focus_monitor = utils.get_mouse_monitor()

        if self.last_focus_monitor == -1:
            self.last_focus_monitor = current_focus_monitor
            return

        if self.unlock_dialog and current_focus_monitor != self.last_focus_monitor:
            self.last_focus_monitor = current_focus_monitor
            self.overlay.queue_resize()

    def add_child_widget(self, widget):
        self.overlay.add_overlay(widget)

    def put_on_top(self, widget):
        self.overlay.reorder_overlay(widget, -1)
        self.overlay.queue_draw()

    def put_on_bottom(self, widget):
        self.overlay.reorder_overlay(widget, 0)
        self.overlay.queue_draw()

    def position_overlay_child(self, overlay, child, allocation):
        if isinstance(child, MonitorView):
            w, h = child.get_preferred_size()
            allocation.x = child.rect.x
            allocation.y = child.rect.y
            allocation.width = child.rect.width
            allocation.height = child.rect.height

            return True

        if isinstance(child, UnlockDialog):
            monitor = utils.get_mouse_monitor()
            monitor_rect = self.screen.get_monitor_geometry(monitor)

            min_rect, nat_rect = child.get_preferred_size()

            allocation.width = nat_rect.width
            allocation.height = nat_rect.height

            allocation.x = monitor_rect.x + (monitor_rect.width / 2) - (nat_rect.width / 2)
            allocation.y = monitor_rect.y + (monitor_rect.height / 2) - (nat_rect.height / 2)

            return True

        if isinstance(child, ClockWidget):
            min_rect, nat_rect = child.get_preferred_size()

            current_monitor = child.current_monitor

            if status.Awake:
                child.set_halign(Gtk.Align.START)
                child.set_valign(Gtk.Align.CENTER)
                current_monitor = utils.get_mouse_monitor()

            monitor_rect = self.screen.get_monitor_geometry(current_monitor)

            allocation.width = nat_rect.width
            allocation.height = nat_rect.height

            halign = child.get_halign()
            valign = child.get_valign()

            if halign == Gtk.Align.START:
                allocation.x = monitor_rect.x
            elif halign == Gtk.Align.CENTER:
                allocation.x = monitor_rect.x + (monitor_rect.width / 2) - (nat_rect.width / 2)
            elif halign == Gtk.Align.END:
                allocation.x = monitor_rect.x + monitor_rect.width - nat_rect.width

            if valign == Gtk.Align.START:
                allocation.y = monitor_rect.y
            elif valign == Gtk.Align.CENTER:
                allocation.y = monitor_rect.y + (monitor_rect.height / 2) - (nat_rect.height / 2)
            elif valign == Gtk.Align.END:
                allocation.y = monitor_rect.y + monitor_rect.height - nat_rect.height

            # utils.debug_allocation(allocation)

            return True

        if isinstance(child, AudioBar):
            min_rect, nat_rect = child.get_preferred_size()

            if status.Awake:
                current_monitor = utils.get_mouse_monitor()
                monitor_rect = self.screen.get_monitor_geometry(current_monitor)
                allocation.x = monitor_rect.x
                allocation.y = monitor_rect.y
                allocation.width = nat_rect.width
                allocation.height = nat_rect.height
            else:
                allocation.x = child.rect.x
                allocation.y = child.rect.y
                allocation.width = nat_rect.width
                allocation.height = nat_rect.height

            return True

        if isinstance(child, InfoBar):
            min_rect, nat_rect = child.get_preferred_size()

            if status.Awake:
                current_monitor = utils.get_mouse_monitor()
                monitor_rect = self.screen.get_monitor_geometry(current_monitor)
                allocation.x = monitor_rect.x + monitor_rect.width - nat_rect.width
                allocation.y = monitor_rect.y
                allocation.width = nat_rect.width
                allocation.height = nat_rect.height
            else:
                allocation.x = child.rect.x + child.rect.width - nat_rect.width
                allocation.y = child.rect.y
                allocation.width = nat_rect.width
                allocation.height = nat_rect.height

            return True


        return False
