from datetime import timedelta
import logging
import voluptuous as vol

from homeassistant.const import (
    CONF_NAME,
)
from homeassistant.util import Throttle
from homeassistant.components.camera import (
    PLATFORM_SCHEMA,
    Camera,
)
from homeassistant.helpers import config_validation as cv

_LOGGER = logging.getLogger(__name__)

CONF_VACUUM_CONNECTION_STRING = "vacuum_ssh"
CONF_VACUUM_ENTITY_ID = "vacuum_entity"

DEFAULT_NAME = "Xiaomi Vacuum Camera"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_VACUUM_CONNECTION_STRING): cv.string,
        vol.Required(CONF_VACUUM_ENTITY_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([VacuumCamera(hass, config)])


class VacuumCamera(Camera):
    def __init__(self, hass, device_info):
        super().__init__()
        self.hass = hass
        self._name = device_info.get(CONF_NAME)
        self._vacuum_entity = device_info.get(CONF_VACUUM_ENTITY_ID)
        self._frame_interval = 5
        self._last_image = None
        self._last_path = None
        self._base = None
        self._center = None
        self._current = None
        vacuum_path = device_info.get(CONF_VACUUM_CONNECTION_STRING)
        temp_dir = "/tmp/hass_xiaomi_map_data"
        self._extractor = Extractor(vacuum_path, temp_dir)
        self.throttled_camera_image = Throttle(timedelta(seconds=5))(self._camera_image)
        self.async_camera_image()

    @property
    def frame_interval(self):
        return self._frame_interval

    def camera_image(self):
        self.throttled_camera_image()
        return self._last_image

    def _camera_image(self):
        vacuum_state = self.hass.states.get(self._vacuum_entity).state
        if vacuum_state != "docked":
            self._extractor.update()
        self._last_image = self._extractor.get_image()
        self.async_schedule_update_ha_state()
        return self._last_image

    @property
    def name(self):
        return self._name

    @property
    def device_state_attributes(self):
        return {
            "mapData": self._extractor.get_parameters(),
            "currentPos": self._extractor.get_current(),
            "vacuum_status": self.hass.states.get(self._vacuum_entity).state
        }


import os
import shutil
import io
import tarfile
from PIL import Image, ImageChops


class Extractor:
    color_move = (238, 247, 255, 255)
    color_dot = (164, 0, 0, 255)
    color_ext_background = (82, 81, 82, 255)
    color_home_background = (35, 120, 198, 255)
    color_wall = (105, 208, 253, 255)
    color_white = (255, 255, 255, 255)
    color_grey = (125, 125, 125, 255)
    color_black = (0, 0, 0, 255)
    color_transparent = (0, 0, 0, 0)

    def __init__(self, vacuum_connection, temp_directory):
        self._vacuum_connection = vacuum_connection
        self._temp = temp_directory
        self._slam_data = None
        self._map_data = None
        self._charger_data = None
        self._center_x = None
        self._center_y = None
        self._charger_pos = None
        self._current_pos = None
        self._png = None
        self._last_map_data = None
        self._parsed_path = []
        self._extracted_archive = False
        self._extracted_slam = False
        self._extracted_charger = False

    def extract(self):
        self._extracted_archive = False
        shutil.rmtree(self._temp + "/usr", True)
        with tarfile.open(self._temp + "/map_data.tar.gz", "r:gz") as tar:
            tar.extractall(self._temp)
        self._extracted_archive = True

    def read_data(self):
        if not self._extracted_archive:
            return
        files = os.listdir(self._temp + "/usr/games/mapdata/")

        self._extracted_slam = False
        slam_file = self._temp + "/usr/games/mapdata/SLAM_fprintf.log"
        if os.path.exists(slam_file):
            with open(slam_file) as slam_data:
                self._slam_data = slam_data.read()
                self._extracted_slam = True

        self._extracted_charger = False
        charger_file = self._temp + "/usr/games/mapdata/ChargerPos.data"
        if os.path.exists(charger_file):
            with open(charger_file) as charger_data:
                self._charger_data = charger_data.read()
                self._extracted_charger = True
        files = list(
            map(lambda v: int(v.replace("navmap", "").replace(".ppm", "")),
                filter(lambda v: v.startswith("navmap"), files)))
        if len(files) != 0:
            map_file = self._temp + "/usr/games/mapdata/navmap{0}.ppm".format(max(files))
            with open(map_file, 'rb') as map_data:
                map_data = map_data.read()
                if len(map_data.split(b"\n")[2]) != 3145728:
                    return
                self._map_data = map_data

    def parse_charger_pos(self):
        if not self._extracted_charger:
            return
        lines = self._charger_data.split("\n")
        x = int(lines[0].replace("x = ", "").replace(";", ""))
        y = int(lines[1].replace("y = ", "").replace(";", ""))
        a = float(lines[2].replace("angle = ", "").replace(";", ""))
        self._charger_pos = {"x": x, "y": y, "a": a}

    def convert_map(self):
        if self._map_data is None:
            return None
        with Image.open(io.BytesIO(self._map_data)) as image:
            map_image = image.convert('RGBA')
        return map_image

    def parse_path(self, map_image):
        self._parsed_path.clear()
        center_x = map_image.size[0] / 2
        center_y = map_image.size[1] / 2
        for line in self._slam_data.split("\n"):
            if 'estimate' in line:
                d = line.split('estimate')[1].strip()
                y, x, z = map(float, d.split(' '))
                x = center_x + (x * 20)
                y = center_y + (y * 20)
                pos = {"x": x, "y": y, "a": z}
                self._parsed_path.append(pos)
        if len(self._parsed_path) > 0:
            self._current_pos = self._parsed_path[len(self._parsed_path) - 1]

    def crop_map(self, map_image):
        center_x = map_image.size[0] / 2
        center_y = map_image.size[1] / 2
        bgcolor_image = Image.new('RGBA', map_image.size, self.color_grey)
        cropbox = ImageChops.subtract(map_image, bgcolor_image).getbbox()
        m = 15
        cropbox_with_margin = (cropbox[0] - m, cropbox[1] - m, cropbox[2] + m, cropbox[3] + m)
        self._center_x = center_x - cropbox_with_margin[0]
        self._center_y = center_y - cropbox_with_margin[1]
        map_image = map_image.crop(cropbox_with_margin)
        return map_image

    def colorize_map(self, map_image):
        pixdata = map_image.load()
        for y in range(map_image.size[1]):
            for x in range(map_image.size[0]):
                if pixdata[x, y] == self.color_grey:
                    pixdata[x, y] = self.color_transparent
                elif pixdata[x, y] == self.color_white:
                    pixdata[x, y] = self.color_home_background
                elif pixdata[x, y] == self.color_black:
                    pixdata[x, y] = self.color_wall
                elif pixdata[x, y] not in [self.color_move, self.color_dot]:
                    pixdata[x, y] = self.color_home_background
        return map_image

    def convert_map_to_png(self, map_image):
        png_image = io.BytesIO()
        map_image.save(png_image, format="png")
        png_image.seek(0)
        data = png_image.read()
        png_image.close()
        return data

    def copy_data(self):
        os.system("mkdir -p {}".format(self._temp))
        os.system("ssh {} '{}' > /dev/null 2>&1".format(self._vacuum_connection, self.script()))
        os.system(
            "scp {}:/usr/games/map_data.tar.gz {}/map_data.tar.gz > /dev/null 2>&1".format(self._vacuum_connection,
                                                                                           self._temp))

    def update(self):
        self.copy_data()
        self.extract()
        self.read_data()
        self.parse_charger_pos()
        try:
            map_data = self.convert_map()
            self._last_map_data = map_data
        except:
            map_data = self._last_map_data
        if map_data is None:
            return
        self.parse_path(map_data)
        map_data = self.colorize_map(map_data)
        map_data = self.crop_map(map_data)
        png = self.convert_map_to_png(map_data)
        self._png = png

    def get_image(self):
        return self._png

    def get_path(self):
        return self._parsed_path

    def get_charger(self):
        return self._charger_pos

    def get_center(self):
        return {"x": self._center_x, "y": self._center_y}

    def get_current(self):
        return self._current_pos

    def get_parameters(self):
        return {
            "center": self.get_center(),
            "charger": self.get_charger(),
            "current": self.get_current(),
            "path": self.get_path()
        }

    @staticmethod
    def script():
        return "output_dir=/usr/games/mapdata; " \
               "function copy_if_exists() { " \
               "if [ ! -f $1 ] ; " \
               "then return 0; " \
               "fi; " \
               "rm $output_dir/$2; " \
               "cp $1 $output_dir; " \
               "}; " \
               "mkdir -p $output_dir; " \
               "rm -f $output_dir/SLAM_fprintf.log; " \
               "copy_if_exists /run/shm/SLAM_fprintf.log SLAM_fprintf.log; " \
               "copy_if_exists /run/shm/navmap*.ppm navmap*.ppm; " \
               "copy_if_exists /mnt/data/rockrobo/ChargerPos.data ChargerPos.data; " \
               "tar -czf $output_dir/../map_data.tar.gz $output_dir/* 2> /dev/null;"
