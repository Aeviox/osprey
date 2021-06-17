from lib.lib import Base, Streamer
from lib.raspi.pi_lib import configure_port, PicamOutput

from random import random
import time


class TestStreamer(Streamer):
    def __init__(self, *args):
        super().__init__(*args)
        self.frames = 10           # how many frames are in each request
        self.frames_sent = 0       # number of frames sent

        self.val_1 = 0
        self.val_2 = 1
        self.val_3 = 2

    def loop(self):
        """ Maine execution loop """
        data = {'time': [], 'val_1': [], 'val_2': [], 'val_3': []}
        for i in range(self.frames):
            self.val_1 += random()-0.5
            self.val_2 += random()-0.5
            self.val_3 += random()-0.5
            data['time'].append(self.time())
            data['val_1'].append(self.val_1)
            data['val_2'].append(self.val_2)
            data['val_3'].append(self.val_3)
            time.sleep(0.05)

        self.database.write_data(self.id, data)


class SenseStreamer(Streamer):
    def __init__(self, *args):
        super().__init__(*args)
        from sense_hat import SenseHat
        self.sense = SenseHat()   # sense hat object
        self.frames = 10           # how many frames are in each request

        self.frames_sent = 0    # number of frames sent

    def loop(self):
        """ Maine execution loop """
        data = {'time': [], 'humidity': [], 'pressure': [], 'temperature': [], 'pitch': [], 'roll': [], 'yaw': []}
        for i in range(self.frames):
            roll, pitch, yaw = self.sense.get_orientation_degrees().values()
            data['humidity'].append(self.sense.get_humidity())
            data['pressure'].append(self.sense.get_pressure())
            data['temperature'].append((self.sense.get_temperature_from_humidity() + self.sense.get_temperature_from_pressure()) / 2)
            data['roll'].append(roll)
            data['pitch'].append(pitch)
            data['yaw'].append(yaw)
            data['time'].append(self.time())

        self.database.write_data(self.id, data)

    def start(self):
        """ Extended from base class in pi_lib.py """
        # enable compass, gyro, and accelerometer to calculate orientation
        self.sense.set_imu_config(True, True, True)
        super().start()

    def stop(self):
        """ Extended from base class in pi_lib.py """
        super().stop()


class LogStreamer(Streamer):
    def __init__(self, *args):
        super().__init__(*args)
        self.handler = 'LogHandler'

        with open(CONFIG_PATH) as config_file:
            config = json.load(config_file)
        self.log_path = config.get('LOG_PATH') + '/log.log'
        self.client_name = config.get('NAME')

    def loop(self):
        """ Main execution loop """
        time.sleep(10)  # send every 10 seconds
        self.send_log()

    def START(self, request):
        """
        HTTPRequest method START
        Start Streaming continually
        Extended from base class in pi_lib.py
        """
        # send initial information
        init_req = HTTPRequest()  # new request
        init_req.add_request('INIT')  # call INIT method on server handler
        init_req.add_header('client', self.client_name)
        self.send(init_req, request.origin)  # send init request back
        self.send_log()  # send first log immediately

        super().START(request)  # start main loop

    def STOP(self, request):
        """
        HTTPRequest method STOP
        Extended from base class in pi_lib.py
        """
        super().STOP(request)  # stop main loop
        self.send_log()  # send remainder of log

    def send_log(self):
        """ Send the contents of the local log file to the requesting socket """
        resp = HTTPRequest()  # new INGEST request
        resp.add_request("INGEST")
        with Base.log_lock:  # get read lock on log file
            with open(self.log_path, 'r+') as file:
                log = file.read()  # get logs
                file.truncate(0)  # erase
        log = log.encode(self.encoding)
        resp.add_content(log)
        self.send(resp)


class VideoStreamer(Streamer):
    def __init__(self, *args):
        super().__init__(*args)
        self.frames_sent = 0    # number of frames sent
        self.start_time = 0           # time of START

        self.picam_buffer = PicamOutput()  # buffer to hold images from the Picam

    def loop(self):
        """
        Main execution loop
        """
        if not self.camera.frame.complete or self.camera.frame.frame_type == self.sps:
            return
        image = self.picam_buffer.read()  # get most recent frame
        self.frames_sent += 1

        data = {
            'time': self.time(),
            'frame': image
        }

        self.database.write_data(self.id, data)

    def start(self):
        """
        HTTPRequest method START
        Start Streaming continually
        Extended from base class in pi_lib.py
        """
        if self.streaming.is_set():
            return

        # for some reason if the PiCamera object is defined on a different thread, start_recording will hang.
        from picamera import PiCamera, PiVideoFrameType
        self.camera = PiCamera(resolution='300x300', framerate=20)
        self.camera.rotation = 180
        self.sps = PiVideoFrameType.sps_header

        # info to send to database
        self.info['framerate'] = self.camera.framerate[0]
        self.info['width'] = self.camera.resolution.width
        self.info['height'] = self.camera.resolution.height

        # start recording
        self.camera.start_recording(self.picam_buffer,
            format='h264', quality=25, profile='constrained', level='4.2',
            intra_period=self.info['framerate'], intra_refresh='both', inline_headers=True, sps_timing=True
        )
        time.sleep(2)  # let camera warm up for a sec. Does weird stuff otherwise.
        super().start()  # Start main loop

    def stop(self):
        """
        HTTPRequest method STOP
        Extended from the base class in pi_lib.py
        """
        super().stop()  # Stop main loop
        try:
            self.camera.stop_recording()
            self.camera.close()  # close camera resources
        except:
            pass


class EEGStreamer(Streamer):
    """
    EEG Streamer class for an OpenBCI board (Cyton, Cyton+Daisy, Ganglion)
    """
    def __init__(self, dev_path, *args):
        """ Dev path is the device path of the dongle"""
        super().__init__(*args)

        from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds

        self.board_id = BoardIds.CYTON_DAISY_BOARD.value   # Cyton+Daisy borad ID (2)

        self.eeg_channel_indexes = BoardShim.get_eeg_channels(self.board_id)  # list of EEG channel indexes
        self.eeg_channel_names = BoardShim.get_eeg_names(self.board_id)       # list of EEG channel names
        self.time_channel = BoardShim.get_timestamp_channel(self.board_id)    # index of timestamp channel
        self.freq = BoardShim.get_sampling_rate(self.board_id)  # sample frequency
        self.serial_port = dev_path

        # BoardShim.enable_dev_board_logger()
        BoardShim.disable_board_logger()  # disable logger

        params = BrainFlowInputParams()
        params.serial_port = dev_path  # serial port of dongle
        self.board = BoardShim(self.board_id, params)  # board object

        # add info to send to database
        self.info['sample_rate'] = self.freq
        self.info['channels'] = ','.join(self.eeg_channel_names)

    def loop(self):
        """ Main execution loop """
        time.sleep(0.25)  # wait a bit for the board to collect another chunk of data
        data = {}
        for channel in self.eeg_channel_names:  # lists of channel data
            data[channel] = []

        # attempt to read from board
        # data collected in uV
        try:
            raw_data = self.board.get_board_data()
        except Exception as e:
            return

        # convert from epoch time to relative time since session start
        data['time'] = list(raw_data[self.time_channel] - self.start_time)

        for i, j in enumerate(self.eeg_channel_indexes):
            data[self.eeg_channel_names[i]] = list(raw_data[j])

        self.database.write_data(self.id, data)

    def start(self):
        """
        HTTPRequest method START
        Start Streaming continually
        Extended from base class in pi_lib.py
        """
        if self.streaming.is_set():
            return

        # configure data collection port to avoid data chunking
        configure_port(self.serial_port)

        # start EEG session
        tries = 0
        while tries <= 5:
            tries += 1
            try:
                self.board.prepare_session()
                break
            except:
                time.sleep(0.1)

        if self.board.is_prepared():
            self.board.start_stream()  # start stream
        else:
            self.throw("Failed to prepare streaming session in {}. Make sure the board is turned on.".format(self), trace=False)
            return

        # First send some initial information to this stream's info channel
        super().start()  # start main loop

    def stop(self):
        """ Extended from base class in pi_lib.py """
        super().stop()  # stop main loop
        try:
            self.board.stop_stream()
            self.board.release_session()
        except:
            pass


class SynthEEGStreamer(Streamer):
    """
    Synthetic EEG streamer class for testing
    """
    def __init__(self, *args):
        super().__init__(*args)
        from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds

        self.board_id = BoardIds.SYNTHETIC_BOARD.value  # synthetic board (-1)

        self.eeg_channel_indexes = BoardShim.get_eeg_channels(self.board_id)  # list of EEG channel indexes
        self.eeg_channel_names = BoardShim.get_eeg_names(self.board_id)  # list of EEG channel names
        self.time_channel = BoardShim.get_timestamp_channel(self.board_id)  # index of timestamp channel
        self.freq = BoardShim.get_sampling_rate(self.board_id)  # sample frequency

        # BoardShim.enable_dev_board_logger()
        BoardShim.disable_board_logger()  # disable logger
        params = BrainFlowInputParams()
        self.board = BoardShim(self.board_id, params)  # board object

        # add info to send to database
        self.info['sample_rate'] = self.freq
        self.info['channels'] = ','.join(self.eeg_channel_names)

    def loop(self):
        """ Main execution loop """
        time.sleep(0.25)  # wait a bit for the board to collect another chunk of data
        data = {}
        for channel in self.eeg_channel_names:  # lists of channel data
            data[channel] = []

        # attempt to read from board
        # data collected in uV
        try:
            raw_data = self.board.get_board_data()
        except Exception as e:
            return

        # convert from epoch time to relative time since session start
        data['time'] = list(raw_data[self.time_channel] - self.start_time)

        for i, j in enumerate(self.eeg_channel_indexes):
            data[self.eeg_channel_names[i]] = list(raw_data[j])

        self.database.write_data(self.id, data)

    def start(self):
        """ Extended from base class in pi_lib.py """
        # start EEG stream if not already
        if not self.streaming.is_set():
            self.board.prepare_session()
            self.board.start_stream()

        # First send some initial information to this stream's info channel
        super().start()  # start main loop

    def stop(self):
        """ Extended from base class in pi_lib.py """
        super().stop()  # stop main loop
        try:
            self.board.stop_stream()
            self.board.release_session()
        except:
            pass


class CytonStreamer(Streamer):
    """
    Streams data from an OpenBCI board equipped with a Pulse sensor (through the analog pins)
    """
    def __init__(self, dev_port, *args):
        super().__init__(*args)

        from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds

        self.board_id = BoardIds.CYTON_BOARD.value   # Cyton board ID (0)
        self.serial_port = dev_port

        # Channels for main board output
        self.board_channel_indexes = BoardShim.get_ecg_channels(self.board_id)
        self.board_channel_names = [str(i) for i in range(len(self.board_channel_indexes))]  # list of ECG channel names

        # Pulse sensor data sent through 3 AUX channels instead
        self.pulse_channel_indexes = BoardShim.get_analog_channels(self.board_id)
        self.pulse_channel_names = ['pulse_0', 'pulse_1', 'pulse_2']

        self.time_channel = BoardShim.get_timestamp_channel(self.board_id)    # index of timestamp channel
        self.freq = BoardShim.get_sampling_rate(self.board_id)  # sample frequency

        self.info['sample_rate'] = self.freq
        self.info['pulse_channels'] = ','.join(self.pulse_channel_names)
        self.info['board_channels'] = ','.join(self.board_channel_names)

        # BoardShim.enable_dev_board_logger()
        BoardShim.disable_board_logger()  # disable logger

        params = BrainFlowInputParams()
        params.serial_port = self.serial_port  # serial port of dongle
        self.board = BoardShim(self.board_id, params)  # board object

    def loop(self):
        """ Main execution loop """
        time.sleep(0.25)  # wait a bit for the board to collect another chunk of data
        data = {}
        for channel in self.pulse_channel_names:  # lists of channel data
            data[channel] = []

        # attempt to read from board
        # data collected in uV
        try:
            raw_data = self.board.get_board_data()
        except Exception as e:
            return

        # convert from epoch time to relative time since session start
        data['time'] = list(raw_data[self.time_channel] - self.start_time)

        for i, j in enumerate(self.pulse_channel_indexes):
            data[self.pulse_channel_names[i]] = list(raw_data[j])

        for i, j in enumerate(self.board_channel_indexes):
            data[self.board_channel_names[i]] = list(raw_data[j])

        self.database.write_data(self.id, data)

    def start(self):
        """
        HTTPRequest method START
        Start Streaming continually
        Extended from base class in pi_lib.py
        """
        if self.streaming.is_set():
            return

        # configure data collection port to avoid data chunking
        configure_port(self.serial_port)

        # start EEG session
        tries = 0
        while tries <= 5:
            tries += 1
            try:
                self.board.prepare_session()
                break
            except:
                time.sleep(0.1)

        if self.board.is_prepared():
            self.board.config_board('/2')  # Set board to Analog mode.
            self.board.start_stream()  # start stream
        else:
            self.throw("Failed to prepare streaming session in {}. Make sure the board is turned on.".format(self), trace=False)
            return

        # First send some initial information to this stream's info channel
        super().start()  # start main loop

    def stop(self):
        """ Extended from base class in pi_lib.py """
        super().stop()  # stop main loop
        try:
            self.board.stop_stream()
            self.board.release_session()
        except:
            pass