import sounddevice as sd
import soundfile as sf
from lib.raspi.pi_lib import BytesOutput
from io import BytesIO
import ffmpeg
from time import sleep


import sounddevice as sd
import soundfile as sf
from lib.raspi.pi_lib import BytesOutput2
from io import BytesIO
from time import sleep

in_buf = BytesIO()
out_buf = BytesIO()
samplerate = 44100
channels = 1

