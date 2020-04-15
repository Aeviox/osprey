import time
from datetime import datetime
import sense_stream


# Define server and log files
server = "http://51.161.8.254:5568"
log = open("/home/pi/Desktop/PiCode/Logs/" + str(datetime.now()), "w+")


# Wait for active internet connection to support headless operation
#while True:
#    try:
#        socket.gethostbyname("google.com")
#        break
#
#    except:
#        time.sleep(1)


# continuously attempt to start connection
while True:
    try:
        sense_stream.stream(server, log)
        
    except Exception as e:
        # Write to log file
        print("Exception caught: ", e, file = log)
        time.sleep(.5)
        
log.close()