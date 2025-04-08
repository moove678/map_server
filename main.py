import math
import logging
import requests

from kivy.uix.screenmanager import Screen
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.button import Button
from kivy.clock import Clock
from kivy_garden.mapview import MapView, MapMarker
from kivy.metrics import dp
from kivy.graphics import Color, Line, InstructionGroup

from plyer import camera
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup

from core.real_gps_handler import RealGPS
from core.utils import headers_with_token, show_info
from ui.nav_bar import add_nav_bar
from ui.shared_popups import create_comment_popup
from ui.popups import SosFormPopup

from kivy.app import App


class MapScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = "map"
        self.main_layout = FloatLayout()
        self.add_widget(self.main_layout)

        app = App.get_running_app()
        t = app.get_translations()

        self.map_view = MapView(lat=0, lon=0, zoom=2)
        self.map_view.size_hint = (1, 1)
        self.map_view.bind(on_touch_down=self.on_map_touch)
        self.main_layout.add_widget(self.map_view)

        self.user_marker = MapMarker(lat=0, lon=0)
        self.map_view.add_widget(self.user_marker)

        self.gps_handler = RealGPS(self.on_gps_location)
        self.gps_started = False
        self.follow_user = True
        self.other_markers = {}

        self.center_btn = Button(
            text=t["center"],
            size_hint=(None, None),
            size=(dp(40), dp(40)),
            pos_hint={"right": 0.98, "y": 0.02},
            background_color=(0, 1, 0, 0.6),
            color=(0, 0, 0, 1)
        )
        self.center_btn.bind(on_press=self.toggle_follow)
        self.main_layout.add_widget(self.center_btn)

        self.sos_btn = Button(
            text=t["sos"],
            size_hint=(None, None),
            size=(dp(100), dp(60)),
            pos_hint={"center_x": 0.5, "y": 0.02},
            background_color=(1, 0, 0, 0.8),
            color=(1, 1, 1, 1),
            font_name=app.UNIVERSAL_FONT
        )
        self.sos_btn.bind(on_press=self.open_sos_form)
        self.main_layout.add_widget(self.sos_btn)

        self.group_btn = Button(
            text=t["group_button"],
            size_hint=(0.3, 0.06),
            pos_hint={"x": 0.02, "top": 0.95},
            font_name=app.UNIVERSAL_FONT,
            background_color=app.theme_bg,
            color=app.theme_fg
        )
        self.group_btn.bind(on_press=self.on_group_btn)
        self.main_layout.add_widget(self.group_btn)

        self.record_btn = Button(
            text="*REC*",
            size_hint=(None, None),
            size=(dp(80), dp(40)),
            pos_hint={"right": 0.98, "top": 0.85},
            background_color=(1, 0, 0, 0.7),
            color=(1, 1, 1, 1)
        )
        self.record_btn.bind(on_press=self.add_comment_with_photo)

        self.route_overlay_widgets = []
        self.route_overlay_instructions = InstructionGroup()
        self.clear_overlay_btn = None

        Clock.schedule_interval(self.update_other_users, 5)
        Clock.schedule_interval(self.auto_center_if_follow, 3)

        add_nav_bar(self)

    def on_pre_enter(self):
        app = App.get_running_app()

        self.clear_route_overlay()
        if app.route_overlay_data:
            self.load_route_overlay(app.route_overlay_data)

        if app.route_handler.is_recording:
            if not self.record_btn.parent:
                self.main_layout.add_widget(self.record_btn)
        else:
            if self.record_btn.parent:
                self.main_layout.remove_widget(self.record_btn)

        if not self.gps_started:
            self.gps_handler.start()
            self.gps_started = True

    def on_gps_location(self, lat, lon):
        self.user_marker.lat = lat
        self.user_marker.lon = lon

        if self.follow_user:
            self.map_view.center_on(lat, lon)

        app = App.get_running_app()
        if app.jwt_token:
            try:
                requests.post(
                    f"{app.SERVER_URL}/update_location",
                    json={"lat": lat, "lon": lon},
                    headers=headers_with_token(app.jwt_token),
                    timeout=3,
                    verify=False
                )
            except Exception as e:
                logging.error(f"[MapScreen] update_location error: {e}")

        if app.route_handler.is_recording:
            app.route_handler.add_point(lat, lon)

    def auto_center_if_follow(self, dt):
        if self.follow_user:
            self.map_view.center_on(self.user_marker.lat, self.user_marker.lon)

    def toggle_follow(self, *_):
        self.follow_user = not self.follow_user
        self.center_btn.background_color = (0, 1, 0, 0.6) if self.follow_user else (0.3, 0.3, 0.3, 0.6)

    def on_map_touch(self, instance, touch):
        if touch.is_double_tap:
            latlon = self.map_view.get_latlon_at(touch.x, touch.y)
            if latlon:
                self.map_view.center_on(*latlon)
                self.map_view.zoom += 1
            return True
        return False

    def update_other_users(self, dt):
        app = App.get_running_app()
        if not app.jwt_token:
            return
        try:
            r = requests.get(
                f"{app.SERVER_URL}/get_users",
                headers=headers_with_token(app.jwt_token),
                timeout=3,
                verify=False
            )
            if r.status_code != 200:
                return
            data = r.json()
            new_users = []
            my_lat, my_lon = self.user_marker.lat, self.user_marker.lon
            for u in data:
                if u["username"] == app.username:
                    continue
                dist = self._dist_km(my_lat, my_lon, u["lat"], u["lon"])
                if dist <= app.user_display_radius:
                    new_users.append(u["username"])
                    if u["username"] in self.other_markers:
                        mk = self.other_markers[u["username"]]
                        mk.lat = u["lat"]
                        mk.lon = u["lon"]
                    else:
                        mk = MapMarker(lat=u["lat"], lon=u["lon"])
                        self.other_markers[u["username"]] = mk
                        self.map_view.add_widget(mk)
            for uname in list(self.other_markers.keys()):
                if uname not in new_users:
                    self.map_view.remove_widget(self.other_markers[uname])
                    del self.other_markers[uname]
        except Exception as e:
            logging.error(f"[MapScreen] update_other_users: {e}")

    def _dist_km(self, lat1, lon1, lat2, lon2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * \
            math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def add_comment_with_photo(self, *_):
        app = App.get_running_app()
        lat, lon = self.user_marker.lat, self.user_marker.lon

        def save_comment(text, photo_path):
            app.route_handler.add_comment(lat, lon, text, photo_path)

        def on_comment_entered(text, *_):
            try:
                photo_path = f"sos_photo_{int(lat * 10000)}_{int(lon * 10000)}.jpg"
                camera.take_picture(filename=photo_path, on_complete=lambda _: save_comment(text, photo_path))
            except Exception as e:
                logging.warning("Камера недоступна: %s", e)
                save_comment(text, None)

        create_comment_popup(callback=on_comment_entered)

    def open_sos_form(self, *_):
        lat, lon = self.user_marker.lat, self.user_marker.lon
        SosFormPopup(lat, lon).open()

    def on_group_btn(self, *_):
        app = App.get_running_app()
        if not app.jwt_token:
            show_info("Нет доступа. Войдите в аккаунт.")
            return
        if app.group_chat_handler and app.group_chat_handler.is_in_group():
            from ui.group_chat_overlay import GroupChatOverlay
            GroupChatOverlay().open()
        else:
            from ui.group_choose_overlay import GroupChooseOverlay
            overlay = GroupChooseOverlay(after_join_callback=self._on_join_group)
            overlay.open()

    def _on_join_group(self, success, _):
        if success:
            from ui.group_chat_overlay import GroupChatOverlay
            GroupChatOverlay().open()

    def load_route_overlay(self, route):
        if route.get("route_points"):
            with self.map_view.canvas.after:
                Color(0, 0, 1, 1)
                points = [(pt["lon"], pt["lat"]) for pt in route["route_points"]]
                if points:
                    line = Line(points=sum(points, ()), width=2)
                    self.route_overlay_instructions.add(line)
            self.map_view.canvas.after.add(self.route_overlay_instructions)

            for pt in [route["route_points"][0], route["route_points"][-1]]:
                mk = MapMarker(lat=pt["lat"], lon=pt["lon"])
                self.route_overlay_widgets.append(mk)
                self.map_view.add_widget(mk)

        for c in route.get("route_comments", []):
            mk = MapMarker(lat=c["lat"], lon=c["lon"])
            self.map_view.add_widget(mk)
            self.route_overlay_widgets.append(mk)

        if not self.clear_overlay_btn:
            self.clear_overlay_btn = Button(
                text="Снять маршрут",
                size_hint=(None, None),
                size=(dp(120), dp(40)),
                pos_hint={"x": 0.02, "y": 0.1},
                background_color=(1, 1, 0, 0.8),
                color=(0, 0, 0, 1)
            )
            self.clear_overlay_btn.bind(on_press=self.clear_route_overlay)
            self.main_layout.add_widget(self.clear_overlay_btn)

    def clear_route_overlay(self, *_):
        app = App.get_running_app()
        app.clear_route_overlay()

        for w in self.route_overlay_widgets:
            try:
                self.map_view.remove_widget(w)
            except Exception:
                pass
        self.route_overlay_widgets.clear()

        if self.clear_overlay_btn:
            self.main_layout.remove_widget(self.clear_overlay_btn)
            self.clear_overlay_btn = None

        self.map_view.canvas.after.remove(self.route_overlay_instructions)
        self.route_overlay_instructions = InstructionGroup()
