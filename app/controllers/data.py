class Sense:
    def __init__(self):
        self.roomTemperature = '--.-'
        self.airPressure = '--.-'
        self.airHumidity = '--.-'
        self.status = ''
        self.date = ''
        self.ip = 0

class Audio:

    def __init__(self):
        self.decibels = '--.-'
        self.status = ''
        self.date = ''

class EventLog:
    def __init__(self):
        self.temperatureStatus = ''
        self.pressStatus = ''
        self.humidStatus = ''
        self.audioStatus = ''

class TriggerSettingsFormData:
    def __init__(self):
        self.audio = "0"
        self.temperature = "0.0"
        self.pressure = "0.0"
        self.humidity = "0.0"