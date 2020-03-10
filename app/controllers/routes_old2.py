from flask import render_template, flash, redirect, url_for, Response, session, jsonify, request
from app import app
from app.controllers.forms import LoginForm, TriggerSettingsForm, RegistrationForm
from app.controllers.video import *
from app.controllers.data import *
from app import mysql
from app import bcrypt
from datetime import datetime
from random import seed, randint
import logging
import mimetypes
import re
import os
import sys
import time

logging.basicConfig(level=logging.INFO)

# BUFF_SIZE is the size of the number of bytes in each mp4 video chunk response
MB = 1 << 20
BUFF_SIZE = 1 * MB  # send 1 MB at a time

# Seed used to for random number generation
seed(1)
LOG = logging.getLogger(__name__)

global loggined
loggined = False

# Data structure for handling audio data
audioData = Audio()
# Data structure for handling temperature data
temperatureData = Temperature()
# Data structure for handling event log data
eventLogData = EventLog()
# Data structure for handling trigger settings form data
triggerSettingsFormData = TriggerSettingsFormData()
    

@app.route('/', methods = ['GET','POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    #-------------------------------------------------------------------------------------------
    # The validate_on_submit() method does all form processing work and returns true when a form
    # is submitted and the browser sends a POST request indicating data is ready to be processed
    #-------------------------------------------------------------------------------------------
    if form.validate_on_submit():
        # A template in the application is used to render flashed messages that Flask stores
        #flash('Login requested for user {}, remember_me={}, password_input={}'.format(
            #form.username.data, form.remember_me.data, form.password.data))
        
        # 1. check DB for user .. if not user in DB, return error
        database_cursor = mysql.connection.cursor()
        database_cursor.execute("SELECT * FROM user where username = "+"'"+form.username.data+"'")
        myresult = database_cursor.fetchone()
        hashed_pw_from_db = myresult[2]
        user_input_pw = form.password.data
        correction_password = bcrypt.check_password_hash(hashed_pw_from_db, user_input_pw)
        if correction_password == False:
            # flash("Wrong password")
            redirect(url_for('login'))
            # return redirect(url_for('livefeed'))
        else:
            # flash("login password work")
            session['user'] = form.username.data
            global loggined
            loggined = True
            return redirect(url_for('livefeed'))
    return render_template('login.html', title='Sign In', form=form)


@app.route('/home')
def home():
    return render_template('home.html')


@app.route('/registration', methods=['GET', 'POST'])
def registration():
    form = RegistrationForm()
    #-------------------------------------------------------------------------------------------
    # The validate_on_submit() method does all form processing work and returns true when a form
    # is submitted and the browser sends a POST request indicating data is ready to be processed
    #-------------------------------------------------------------------------------------------
    if form.validate_on_submit():
        # A template in the application is used to render flashed messages that Flask stores
        # flash('registration requested for user {}, password={}, password_confirm={}'.format(
        #    form.username.data, form.password.data, form.password_confirm.data))

        if form.password.data != form.password_confirm.data:
            flash("Password confirmation and password need to be the same")
            return redirect(url_for('registration'))

        # Password Hashing
        inputted_password = form.password.data
        pw_hash = bcrypt.generate_password_hash(inputted_password).decode('utf-8')

        username = form.username.data

        database_cursor = mysql.connection.cursor()
        database_cursor.execute('''CREATE TABLE IF NOT EXISTS user (id INTEGER UNSIGNED AUTO_INCREMENT PRIMARY KEY , username VARCHAR(60), hashed_pw TEXT)''')
        database_cursor.execute(" INSERT INTO user (username,hashed_pw) VALUES ("+ '"'+ username +'"' + ","+ '"'+pw_hash +'"' +")")

        #INSERT INTO Persons (FirstName,LastName)
        #VALUES ('Lars','Monsn');
        # https://www.w3schools.com/python/python_mysql_select.asp

        mysql.connection.commit()

        return redirect(url_for('login'))
    return render_template('registration.html', title='Registration', form=form)


@app.route('/livefeed', methods=['GET', 'POST'])
def livefeed():
    if session.get('user') == True:
        return redirect(url_for('login'))

    if loggined != True:
        return redirect(url_for('login'))

    return render_template('livefeed.html', temperatureData = temperatureData, audioData = audioData)



    #def generate_video():
    #    with open("/root/capstone-site/site/static/video/107-8.mp4", "rb") as f:
    #        while True:
    #            chunk = ... # read each chunk or break if EOF
    #            yield chunk

    #return Response(stream_with_context(generate_video()), mimetype="video/mp4")


@app.route('/archives')
def archives():
    return archive(None)


@app.route('/archive/<int:archive_id>')
def archive(archive_id):
    if loggined != True:
        return redirect(url_for('login'))
    if archive_id == None:
        print("Archive id is None")
    else:
        print("archive_id: ", archive_id)

    if session.get('user') == True:
        return redirect(url_for('login'))
    
    # what are the recent recorded sessions
    database_cursor = mysql.connection.cursor()
    database_cursor.execute("""SELECT id, StartDate FROM Session ORDER BY StartDate DESC LIMIT 5;""")
    db_result = database_cursor.fetchall()

    template_data = []
    if db_result != None:
        for session_data in db_result:
            template_data.append(dict(
                id = session_data[0],
                time = session_data[1].strftime("%m/%d/%Y @ %H:%M:%S")
            ))
    
    print("Template data:")
    print(template_data)

    return render_template('archives.html', 
        sessions=template_data,
        session_id= (archive_id if archive_id != None else -1))


@app.route('/video_feed')
def video_feed():
    """Video streaming route. Put this in the src attribute of an img tag."""
    return Response(gen(Camera(-1, False)),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/session_feed/<int:session_id>')
def session_feed(session_id):
    """Video streaming route. Put this in the src attribute of an img tag."""
    return Response(gen(Camera(session_id, True)),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


"""route is used to start the live stream"""
@app.route('/start')
def start():
    write_token("START")
    print("pressed Start")
    return {}


"""route is used to stop the live stream"""
@app.route('/stop')
def stop():
    print("pressed Stop")
    write_token("STOP")
    return {}


def write_token(token_value):
    database_cursor = mysql.connection.cursor()
    database_cursor.execute("""CREATE TABLE IF NOT EXISTS `Token` (id int(11) NOT NULL AUTO_INCREMENT, Value TEXT NOT NULL, PRIMARY KEY (id));""")
    mysql.connection.commit()

    # insert new token
    sql = "INSERT INTO `Token` (`Value`) VALUES (%s)"
    database_cursor.execute(sql, (token_value,))
    mysql.connection.commit()


"""route is used to update temperature values in the live stream page"""
@app.route('/update_sense', methods=['GET', 'POST'])
def update_sense():
    for _ in range(10):
        value = randint(0, 4)
        temperatureData.skinTemperatureSub1 = "98." + str(value)

    for _ in range(10):
        value = randint(0, 6)
    temperatureData.skinTemperatureSub2 = "98." + str(value)

    for _ in range(10):
        value = randint(0, 3)
        temperatureData.roomTemperature = "75." + str(value)

    temperatureData.status = request.form['status']
    temperatureData.date = request.form['date']

    return jsonify({'result' : 'success', 'status' : temperatureData.status, 'date' : temperatureData.date, 'roomTemperature' : temperatureData.roomTemperature, 'skinTemperatureSub1' : temperatureData.skinTemperatureSub1, 
        'skinTemperatureSub2' : temperatureData.skinTemperatureSub2})


"""route is used to update audio values in the live stream page"""
@app.route('/update_audio', methods=['GET', 'POST'])
def update_audio():
    for _ in range(10):
        value = randint(0, 5)
        audioData.decibels = "6" + str(value)

    audioData.status = request.form['status']
    audioData.date = request.form['date']

    return jsonify({'result' : 'success', 'status' : audioData.status, 'date' : audioData.date, 'decibels' : audioData.decibels})


"""route is used to collect trigger settings from the live stream page"""
@app.route('/update_triggersettings', methods=['POST'])
def update_triggersettings():
    triggerSettingsFormData.audio = request.form['audio_input']
    triggerSettingsFormData.temperature = request.form['temperature_input']

    return jsonify({'result' : 'success', 'audio_input' : triggerSettingsFormData.audio, 'temperature_input' : triggerSettingsFormData.temperature})


"""route is used to update the event log with temperature data"""
@app.route('/update_eventlog_temperature', methods=['GET', 'POST'])
def update_eventlog_temperature():
    alerts = []

    eventLogData.temperatureStatus = request.form['status']

    if (eventLogData.temperatureStatus == 'ON' and temperatureData.status == 'ON'):
        #print('ALL TEMPERATURE ON')
        if (temperatureData.roomTemperature > triggerSettingsFormData.temperature):
            alerts.append("Temperature Trigger: Room temperature exceeded " + triggerSettingsFormData.temperature + " ℉ @ " + temperatureData.date)

        if (temperatureData.skinTemperatureSub1 > triggerSettingsFormData.temperature):
            alerts.append("Temperature Trigger: Subject 1 skin temperature exceeded " + triggerSettingsFormData.temperature + " ℉ @ " + temperatureData.date)

        if (temperatureData.skinTemperatureSub2 > triggerSettingsFormData.temperature):
            alerts.append("Temperature Trigger: Subject 2 skin temperature exceeded " + triggerSettingsFormData.temperature + " ℉ @ " + temperatureData.date)

    return render_template('section.html', messages = alerts)


"""route is used to update the event log with audio data"""
@app.route('/update_eventlog_audio', methods=['GET', 'POST'])
def update_eventlog_audio():
    alerts = []

    eventLogData.audioStatus = request.form['status']

    if (eventLogData.audioStatus == 'ON' and audioData.status == 'ON'):
        #print('ALL AUDIO ON')
        if (audioData.decibels > triggerSettingsFormData.audio):
            alerts.append("Audio Trigger: Audio exceeded " + triggerSettingsFormData.audio + " dB @ " + audioData.date)

    return render_template('section.html', messages = alerts)


















#global_start 
bytes_so_far = 0

@app.route('/test', methods=['GET'])
def test():
    return render_template('test.html')

@app.route('/fetchvideo', methods=['GET'])
def fetchvideo():
    print("FETCHING VIDEO")
    path = "/root/capstone-site/site/static/video/109-4.mp4"
    start, end = get_range(request)
    #return partial_response(path, start, end)
    return partial_response(path, start, None)

def get_range(request):
    range = request.headers.get('Range')
    LOG.info('Requested: %s', range)
    m = re.match('bytes=(?P<start>\d+)-(?P<end>\d+)?', range)
    if m:
        start = m.group('start')
        end = m.group('end')
        start = int(start)
        if end is not None:
            end = int(end)
        return start, end
    else:
        return 0, None


def partial_response(path, start, end=None):
    LOG.info('Requested: %s, %s', start, end)
    file_size = os.path.getsize(path)

    # Determine (end, length)
    if end is None:
        end = start + BUFF_SIZE - 1
    end = min(end, file_size - 1)
    end = min(end, start + BUFF_SIZE - 1)
    length = end - start + 1

    # start is the bytes number requested by the browser
    # Read file
    with open(path, 'rb') as fd:
        fd.seek(start)
        bytes = fd.read(length)
        print("len(bytes): " + str(len(bytes)))
        print(BUFF_SIZE)
    assert len(bytes) == length

    if len(bytes) < BUFF_SIZE: # if last read on an image, read into the next image
        pass
        print("Sending next segment in its entirety")
        # send the first chunk of the next image
        next_path = "/root/capstone-site/site/static/video/107-2.mp4"
        #start, end = get_range(request)
        with open(next_path, 'rb') as f:
            f.seek(0)
            num_to_read = os.path.getsize(next_path) #min(os.path.getsize(next_path) - 1, BUFF_SIZE - 1)
            next_bytes = f.read(BUFF_SIZE)
        return video_response(next_bytes, start, start + BUFF_SIZE, file_size + BUFF_SIZE, path)
    else:
        return video_response(bytes, start, end, file_size, path)
    


def video_response(bytes, start_byte, end_byte, file_size, path):
    response = Response(
        bytes,
        206,
        mimetype=mimetypes.guess_type(path)[0],
        direct_passthrough=True,
    )
    response.headers.add(
        'Content-Range', 'bytes {0}-{1}/{2}'.format(
            #start, end, file_size,
            start_byte, end_byte, file_size,
        ),
    )
    response.headers.add(
        'Accept-Ranges', 'bytes'
    )
    #LOG.info('Response: %s', response)
    LOG.info('Response: %s', response.headers)
    return response

