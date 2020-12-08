from threading import Thread
from contextlib import suppress
from astropy import units as u
from Pyro5.api import Proxy

from panoptes.utils import error
from panoptes.utils import CountdownTimer
from panoptes.utils import get_quantity_value

from panoptes.pocs.camera import AbstractCamera

from huntsman.pocs.filterwheel.pyro import FilterWheel as PyroFilterWheel
from huntsman.pocs.focuser.pyro import Focuser as PyroFocuser
from huntsman.pocs.utils.logger import logger
from huntsman.pocs.utils.pyro.event import RemoteEvent
from huntsman.pocs.utils.pyro import serializers  # Required to set up the custom (de)serializers


class Camera(AbstractCamera):
    """A python remote object (pyro) camera client.

    This class should be instantiated on the main control computer that is
    running POCS, namely via an `Observatory` object.
    """

    def __init__(self, uri, name='Pyro Camera', model='pyro', port=None, *args, **kwargs):
        self.logger = logger
        # The proxy used for communication with the remote instance.
        self._uri = uri
        self.logger.debug(f'Connecting to {port} at {self._uri}')

        super().__init__(name=name, port=port, model=model, logger=self.logger, *args, **kwargs)

        # Hardware that may be attached in connect method.
        self.focuser = None
        self.filterwheel = None

        # Connect to camera
        self.connect()

    # Properties
    @property
    def _proxy(self):
        return Proxy(self._uri)

    @property
    def egain(self):
        return self._proxy.get("egain")

    @property
    def bit_depth(self):
        return self._proxy.get("bit_depth")

    @property
    def temperature(self):
        """
        Current temperature of the camera's image sensor.
        """
        return self._proxy.get("temperature")

    @property
    def target_temperature(self):
        """
        Current value of the CCD set point, the target temperature for the camera's
        image sensor cooling control.

        Can be set by assigning an astropy.units.Quantity.
        """
        return self._proxy.get("target_temperature")

    @target_temperature.setter
    def target_temperature(self, target):
        self._proxy.set("target_temperature", target)

    @property
    def temperature_tolerance(self):
        return self._proxy.get("temperature_tolerance")

    @temperature_tolerance.setter
    def temperature_tolerance(self, tolerance):
        with suppress(AttributeError):
            # Base class constructor is trying to set a default temperature temperature
            # before self._proxy exists, & it's up to the remote camera to do that anyway.
            self._proxy.set("temperature_tolerance", tolerance)

    @property
    def cooling_enabled(self):
        """
        Current status of the camera's image sensor cooling system (enabled/disabled).

        For some cameras it is possible to change this by assigning a boolean
        """
        return self._proxy.get("cooling_enabled")

    @cooling_enabled.setter
    def cooling_enabled(self, enabled):
        self._proxy.set("cooling_enabled", bool(enabled))

    @property
    def cooling_power(self):
        """
        Current power level of the camera's image sensor cooling system (typically as
        a percentage of the maximum).
        """
        return self._proxy.get("cooling_power")

    @property
    def is_exposing(self):
        return self._proxy.get("is_exposing")

    @is_exposing.setter
    def is_exposing(self, is_exposing):
        """Set or clear the remote exposure event."""
        if is_exposing:
            self._exposure_event.set()
        else:
            self._exposure_event.clear()

    @property
    def is_temperature_stable(self):
        return self._proxy.get("is_temperature_stable")

    @property
    def is_ready(self):
        """
        True if camera is ready to start another exposure, otherwise False.
        """
        return self._proxy.get("is_ready")

    # Methods

    def connect(self):
        """ Connect to the distributed camera.
        """
        # Force camera proxy to connect by getting the camera uid.
        # This will trigger the remote object creation & (re)initialise the camera & focuser,
        # which can take a long time with real hardware.
        uid = self._proxy.get_uid()
        if not uid:
            self.logger.error(f"Could't connect to {self.name} on {self._uri}, no uid found.")
            return

        # Retrieve and locally cache camera properties that won't change.
        self._serial_number = uid
        self.name = self._proxy.get("name")
        self.model = self._proxy.get("model")
        self._readout_time = self._proxy.get("readout_time")
        self._file_extension = self._proxy.get("file_extension")
        self._is_cooled_camera = self._proxy.get("is_cooled_camera")
        self._filter_type = self._proxy.get("filter_type")

        # Set up proxies for remote camera's events required by base class
        self._exposure_event = RemoteEvent(self._uri, event_type="camera")
        self._focus_event = RemoteEvent(self._uri, event_type="focuser")

        self._connected = True
        self.logger.debug(f"{self} connected.")

        if self._proxy.has_focuser:
            self.focuser = PyroFocuser(camera=self)

        if self._proxy.has_filterwheel:
            self.filterwheel = PyroFilterWheel(camera=self)

    def take_exposure(self, seconds=1.0 * u.second, filename=None, dark=False, blocking=False,
                      *args, **kwargs):
        """Take an exposure for given number of seconds and saves to provided filename.

        Args:
            seconds (u.second, optional): Length of exposure.
            filename (str, optional): Image is saved to this filename.
            dark (bool, optional): Exposure is a dark frame, default False. On cameras that support
                taking dark frames internally (by not opening a mechanical shutter) this will be
                done, for other cameras the light must be blocked by some other means. In either
                case setting dark to True will cause the `IMAGETYP` FITS header keyword to have
                value 'Dark Frame' instead of 'Light Frame'. Set dark to None to disable the
                `IMAGETYP` keyword entirely.
            blocking (bool, optional): If False (default) returns immediately after starting
                the exposure, if True will block (on the client-side) until it completes.

        Returns:
            threading.Thread: The readout thread, which joins once readout has finished.
        """
        # Start the exposure
        self.logger.debug(f'Taking {seconds} second exposure on {self}: {filename}')

        # Remote method call to start the exposure
        self._proxy.take_exposure(seconds=seconds, filename=filename, dark=dark, *args, **kwargs)

        # Start the readout thread
        timeout = get_quantity_value(seconds, u.second) + self.readout_time + self._timeout
        readout_thread = Thread(target=self._wait_for_readout, args=(timeout,))
        readout_thread.start()
        if blocking:
            readout_thread.join()

        return readout_thread

    def autofocus(self, blocking=False, timeout=None, coarse=False, *args, **kwargs):
        """
        Focuses the camera using the specified merit function. Optionally performs
        a coarse focus to find the approximate position of infinity focus, which
        should be followed by a fine focus before observing.

        Args:
            seconds (scalar, optional): Exposure time for focus exposures, if not
                specified will use value from config.
            focus_range (2-tuple, optional): Coarse & fine focus sweep range, in
                encoder units. Specify to override values from config.
            focus_step (2-tuple, optional): Coarse & fine focus sweep steps, in
                encoder units. Specify to override values from config.
            thumbnail_size (int, optional): Size of square central region of image
                to use, default 500 x 500 pixels.
            keep_files (bool, optional): If True will keep all images taken
                during focusing. If False (default) will delete all except the
                first and last images from each focus run.
            take_dark (bool, optional): If True will attempt to take a dark frame
                before the focus run, and use it for dark subtraction and hot
                pixel masking, default True.
            merit_function (str, optional): Merit function to use as a
                focus metric, default vollath_F4.
            merit_function_kwargs (dict, optional): Dictionary of additional
                keyword arguments for the merit function.
            mask_dilations (int, optional): Number of iterations of dilation to perform on the
                saturated pixel mask (determine size of masked regions), default 10
            coarse (bool, optional): Whether to perform a coarse focus, otherwise will perform
                a fine focus. Default False.
            make_plots (bool, optional: Whether to write focus plots to images folder, default
                False.
            blocking (bool, optional): Whether to block (on the client-side) until autofocus
                complete, default False.
            timeout (float, optional): The client-side autofocus timeout. The default value of `None` will lookup the `focusing.<focus_type>.timeout` value in the config server. If not provided, a default fallback of 600 seconds is used.

        Returns:
            threading.Event: Event that will be set when autofocusing is complete

        Raises:
            ValueError: If invalid values are passed for any of the focus parameters.
        """
        if self.focuser is None:
            msg = "Camera must have a focuser for autofocus!"
            self.logger.error(msg)
            raise AttributeError(msg)

        if timeout is None:
            coarse_str = "coarse" if coarse else "fine"
            timeout = self.get_config(f"focusing.{coarse_str}.timeout", default=600)

        # Remote method call to start the exposure
        self.logger.debug(f'Starting autofocus on {self} with timeout: {timeout}.')
        self._proxy.autofocus(blocking=False, coarse=coarse, *args, **kwargs)
        if blocking:
            self._proxy.event_wait("focuser", timeout=timeout)

        return self._focus_event

    # Private Methods
    def _wait_for_readout(self, timeout, sleep_time=0.1):
        """ Wait for readout to finish on the camera service.
        Args:
            timeout (float): The readout timeout in seconds.
        """
        proxy = self._proxy
        timer = CountdownTimer(timeout)
        while not timer.expired():
            if not proxy.is_reading_out:
                return
            timer.sleep(sleep_time)
        raise error.PanError(f"Timeout of {timeout} reached while waiting for readout to finish"
                              f" on camera client {self}.")

    def _start_exposure(self, **kwargs):
        """Dummy method on the client required to overwrite @abstractmethod"""
        pass

    def _readout(self, **kwargs):
        """Dummy method on the client required to overwrite @abstractmethod"""
        pass

    def _set_cooling_enabled(self):
        """Dummy method required by the abstract class"""
        raise NotImplementedError

    def _set_target_temperature(self):
        """Dummy method required by the abstract class"""
        raise NotImplementedError