import os
import re
import sys
import time
import logging
import mimetypes
import subprocess
import threading
import urllib.request
import json

from queue import Queue
from datetime import datetime
from random import seed, randint
from app import app, mysql, bcrypt
from werkzeug.utils import secure_filename
from app.main.program import Program
from app.views.forms import LoginForm, TriggerSettingsForm, RegistrationForm
from flask import render_template, flash, redirect, url_for, Response, session, jsonify, request, send_file, send_from_directory

# Views
from app.views.home_view import HomeView
from app.views.livefeed_view import LivefeedView
from app.views.login_view import LoginView

# Controllers
from app.controllers.login_controller import LoginController

# BUFF_SIZE is the size of the number of bytes in each mp4 video chunk response
MB = 1 << 20
# Send 1 MB at a time
BUFF_SIZE = 1 * MB
# Seed used for random number generation
seed(1)
# global_start 
bytes_so_far = 0

# Only .py files are allowed to be uploaded
ALLOWED_EXTENSIONS = set(['py'])

LOG = logging.getLogger(__name__)
global loginStatus
loginStatus = True # Avoid login for DECS -- should be false


@app.route('/', methods = ['GET','POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST' and LoginForm().validate_on_submit():
        return LoginController().handle_response()
    else:
        return LoginView().get_rendered_template()


@app.route('/home', methods=['GET'])
def home():
    return HomeView().get_rendered_template()


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
    if session.get('username') == True:
        return redirect(url_for('login'))

    if loginStatus != True:
        return redirect(url_for('login'))

    return LivefeedView().get_rendered_template()



    #def generate_video():
    #    with open("/root/capstone-site/site/static/video/107-8.mp4", "rb") as f:
    #        while True:
    #            chunk = ... # read each chunk or break if EOF
    #            yield chunk

    #return Response(stream_with_context(generate_video()), mimetype="video/mp4")

@app.route('/delete_session/<int:session_id>', methods=['POST'])
def delete_session(session_id):
    database_cursor = mysql.connection.cursor()
    database_cursor.execute('''DELETE FROM Session WHERE id = %s''', (session_id,))
    database_cursor.execute('''DELETE FROM SessionSensor WHERE SessionId = %s''', (session_id,))
    mysql.connection.commit()
    # TODO: actually remove all the data from db and disk
    return Response()

@app.route('/archives')
def archives():
    return archive(None)


@app.route('/archive/<int:archive_id>')
def archive(archive_id):
    print("Entered archive route")
    login_auth = """
    if loginStatus != True:
        return redirect(url_for('login'))
    if archive_id == None:
        print("Archive id is None")
    else:
        print("archive_id: ", archive_id)
    
    if session.get('username') == True:
        return redirect(url_for('login'))
    """
    
    # recent recorded sessions
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
    
    session_id = archive_id if archive_id != None else -1
    database_cursor.execute("""SELECT id, Name, INET_NTOA(IP), SessionId, SensorType FROM SessionSensor WHERE SessionId = %s;""", (session_id,))
    session_sensors = database_cursor.fetchall()

    session_sensors_serialized = json.dumps(session_sensors, separators=(',', ':'))
    #print(session_sensors_serialized)

    session_start_time = -1
    session_end_time = -1
    sql = """SELECT id, StartDate, EndDate FROM Session WHERE StartDate = (SELECT MAX(StartDate) FROM Session);"""
    database_cursor.execute(sql)
    session_info = database_cursor.fetchone()
    if session_id != -1:
        epoch = datetime.utcfromtimestamp(0)
        session_start_time = int((session_info[1] - epoch).total_seconds() * 1000.0) + 1000
        session_end_time = int((session_info[2] - epoch).total_seconds() * 1000.0) - 1000

    cameras = []
    microphones = []
    sense_hats = []
    for session_sensor in session_sensors:
        sensor_type = session_sensor[4]
        sensor_name = session_sensor[1]

        if sensor_type == 'PiCamera':
            sql = """SELECT LastFrameNumber FROM VideoFrames WHERE FirstFrameTimestamp = (SELECT MAX(FirstFrameTimestamp) FROM VideoFrames WHERE SensorId = %s);"""
            database_cursor.execute(sql, (session_sensor[0],))
            result = database_cursor.fetchone()
            last_frame_number = 0
            if result:
                last_frame_number = result[0]

            camera_view_data = dict(
                sensor_id = session_sensor[0],
                sensor_type = sensor_type,
                last_frame_number = last_frame_number,
                name = sensor_name
            )
            cameras.append(camera_view_data)
        elif sensor_type == 'Microphone':
            mic_view_data = dict(
                sensor_id = session_sensor[0],
                sensor_type = sensor_type,
                name = sensor_name
            )
            microphones.append(mic_view_data)
        elif sensor_type == "SenseHat":
            sense_view_data = dict(
                sensor_id = session_sensor[0],
                sensor_type = sensor_type,
                name = sensor_name
            )
            sense_hats.append(sense_view_data)

    sensor_selections = []
    list_index = 1
    for sensor in (cameras + microphones + sense_hats):
        sensor_selections.append(dict(
            sensor_id = sensor['sensor_id'],
            sensor_type = sensor['sensor_type'],
            sensor_name = sensor['name'],
            index = list_index
        ))
        list_index = list_index + 1
    
    print("Sensor selections")
    print(sensor_selections)

    return render_template('archives.html',
        sessions = template_data,
        session_id = (archive_id if archive_id != None else -1),
        session_sensors_serialized = session_sensors_serialized,
        session_start_time = session_start_time,
        session_end_time = session_end_time,
        cameras = cameras,
        microphones = microphones,
        sense_hats = sense_hats,
        sensor_selections = sensor_selections
    )


@app.route('/livestream_config', methods=['GET', 'POST'])
def livestream_config():
    # TODO: input validation
    config_tokens = request.form['livestream_config'].split('&')
    config_json = {
        'cameras': [],
        'microphones': [],
        'sense_hats': []
    }

    if len(request.form['livestream_config']) == 0:
        return jsonify({})
    

    index = 0
    for token in config_tokens:
        key = token.split("=")[0]
        value = token.split("=")[1].replace("%20", " ")

        print(key + ": " + value)
        if 'cam-ip-input' in token:
            name = config_tokens[index + 1].split("=")[1].replace("%20", " ")

            metadata = {'name': name, 'ip': value}
            config_json['cameras'].append(metadata)
        elif 'mic-ip-input' in token:
            name = config_tokens[index + 1].split("=")[1].replace("%20", " ")
            metadata = {'name': name, 'ip': value}
            config_json['microphones'].append(metadata)
        elif 'sen-ip-input' in token:
            name = config_tokens[index + 1].split("=")[1].replace("%20", " ")
            metadata = {'name': name, 'ip': value}
            config_json['sense_hats'].append(metadata)

        index = index + 1
    
    compacted_json = json.dumps(config_json, separators=(',', ':'))
    # Instantiating an object that can execute SQL statements
    database_cursor = mysql.connection.cursor()
    database_cursor.execute("""SELECT id FROM Session WHERE id = (SELECT MAX(id) FROM Session)""")
    result = database_cursor.fetchone()
    session_id = 1 if result == None else result[0] + 1

    for metadata in config_json['cameras']:
        ip = metadata['ip']
        name = metadata['name']
        sql = "INSERT INTO SessionSensor (`IP`, `Name`, `SessionId`, `SensorType`) VALUES (INET_ATON(%s), %s, %s, %s);"
        database_cursor.execute(sql, (ip, name, session_id, "PiCamera"))
    
    for metadata in config_json['microphones']:
        ip = metadata['ip']
        name = metadata['name']
        sql = "INSERT INTO SessionSensor (`IP`, `Name`, `SessionId`, `SensorType`) VALUES (INET_ATON(%s), %s, %s, %s);"
        database_cursor.execute(sql, (ip, name, session_id, "Microphone"))
    
    for metadata in config_json['sense_hats']:
        ip = metadata['ip']
        name = metadata['name']
        sql = "INSERT INTO SessionSensor (`IP`, `Name`, `SessionId`, `SensorType`) VALUES (INET_ATON(%s), %s, %s, %s);"
        database_cursor.execute(sql, (ip, name, session_id, "SenseHat"))

    
    sql = "INSERT INTO Session (`StartDate`, `SensorConfig`) VALUES (NOW(3), %s);"
    database_cursor.execute(sql, (compacted_json,))
    mysql.connection.commit()

    sql = "SELECT id, INET_NTOA(IP), SessionId, SensorType FROM SessionSensor WHERE SessionId = %s"
    database_cursor.execute(sql, (session_id,))
    session_sensors = database_cursor.fetchall()

    return jsonify(session_sensors)


@app.route('/update_sense', methods=['GET', 'POST'])
def update_sense():

    # Indicates if the sense switch has been turned on or not
    status = request.form['status']

    # The IP Address of the sensor
    ip_address = request.form['ipAddress']

    # The stream number of the sensor
    stream_number = request.form['streamNumber']

    # The scalar trigger settings for temperature, pressure, and humidity
    triggerSettings_temperature = session.get('triggerSettings_temperature')
    triggerSettings_pressure = session.get('triggerSettings_pressure')
    triggerSettings_humidity = session.get('triggerSettings_humidity')

    # The id of the logged in user
    user_id = session.get('user_id')

    # The initial measurements until set
    roomTemperature = 0
    airPressure = 0
    airHumidity = 0

    try:
        # Instantiating an object that can execute SQL statements
        database_cursor = mysql.connection.cursor()

        sql = """
            SELECT Temp, Press, Humid, Time 
            FROM Sense 
            WHERE IP = INET_ATON(%s) 
            ORDER BY Time DESC;
        """

        # Get current Sense HAT data from DB
        database_cursor.execute(sql, (ip_address,))
        temp, press, humid, time = database_cursor.fetchone()

        # Convert to JQueryable objects
        roomTemperature = "{:.2f}".format(temp)
        airPressure = "{:.2f}".format(press)
        airHumidity = "{:.2f}".format(humid)

        if (triggerSettings_temperature != '') and (float(roomTemperature) > float(triggerSettings_temperature)):
            # Write temperature data to database
            sql = """
                INSERT INTO eventlog 
                (user_id, alert_time, alert_type, alert_message) 
                VALUES (%s, NOW(), %s, %s);
            """
            message = "Sense " + stream_number + " Temperature exceeded " + triggerSettings_temperature + " F"

            database_cursor.execute(sql, (user_id, "Temperature", message))
            mysql.connection.commit()

        if (triggerSettings_pressure != '') and (float(airPressure) > float(triggerSettings_pressure)):
            # Write pressure data to database
            sql = """
                INSERT INTO eventlog 
                (user_id, alert_time, alert_type, alert_message) 
                VALUES (%s, NOW(), %s, %s);
            """
            message = "Sense " + stream_number + " Pressure exceeded " + triggerSettings_pressure + " millibars"

            database_cursor.execute(sql, (user_id, "Pressure", message))
            mysql.connection.commit()

        if (triggerSettings_humidity != '') and (float(airHumidity) > float(triggerSettings_humidity)):
            # Write humidity data to database
            sql = """
                INSERT INTO eventlog 
                (user_id, alert_time, alert_type, alert_message) 
                VALUES (%s, NOW(), %s, %s);
            """
            message = "Sense " + stream_number + " Humidity exceeded " + triggerSettings_humidity + " %"
            database_cursor.execute(sql, (user_id, "Humidity", message))
            mysql.connection.commit()
    
    # Don't fail out of website on Sense HAT error
    except Exception as e:
        exc_type, exc_obj, tb = sys.exc_info()
        lineno = tb.tb_lineno
        print("Sense HAT " + stream_number + " broken:", e, lineno)
        pass

    return jsonify({'result' : 'success', 'status' : status, 'roomTemperature' : roomTemperature,
    'airPressure': airPressure, 'airHumidity': airHumidity, 'ip': ip_address})


"""route is used to update audio values in the live stream page"""
@app.route('/update_audio', methods=['GET', 'POST'])
def update_audio():

    # Instantiating an object that can execute SQL statements
    database_cursor = mysql.connection.cursor()

    # The scalar trigger setting for audio
    triggerSettings_audio = session.get('triggerSettings_audio')
    # The id of the logged in user
    user_id = session.get('user_id')

    # Indicates whether audio has been turned on or not
    status = request.form['status']

    # The initial decibel level until set
    decibels = 0

    
    for _ in range(10):
        value = randint(0, 5)
        decibels = "6" + str(value)

    if (triggerSettings_audio != '') and (int(decibels) > int(triggerSettings_audio)):
        # Write audio data to database
        database_cursor.execute("INSERT INTO eventlog (user_id, alert_time, alert_type, alert_message) VALUES ('{}', NOW(), '{}', '{}');".format(user_id, "Audio", 
            "Audio exceeded " + triggerSettings_audio + " dB"))
        mysql.connection.commit()

    return jsonify({'result' : 'success', 'status' : status, 'decibels' : decibels})


"""route is used to collect trigger settings from the live stream page"""
@app.route('/update_triggersettings', methods=['POST'])
def update_triggersettings():
    # Updating trigger settings in the session
    session['triggerSettings_audio'] = request.form['audio_input']
    session['triggerSettings_temperature'] = request.form['temperature_input']
    session['triggerSettings_pressure'] = request.form['pressure_input']
    session['triggerSettings_humidity'] = request.form['humidity_input']

    return jsonify({'result' : 'success', 'audio_input' : session.get('triggerSettings_audio'), 'temperature_input' : session.get('triggerSettings_temperature'), 
        'pressure_input' : session.get('triggerSettings_pressure'), 'humidity_input' : session.get('triggerSettings_humidity')})


"""route is used to update the event log for all data types"""
@app.route('/update_eventlog', methods=['GET'])
def update_eventlog():
    # Instantiating an object that can execute SQL statements
    database_cursor = mysql.connection.cursor()

    # The id of the logged in user
    user_id = session.get('user_id')

    #Return the latest 15 event log entries for this user
    sql = """
        SELECT alert_message, alert_time
        FROM eventlog 
        WHERE user_id = %s
        ORDER BY alert_time DESC 
        LIMIT 15;
    """
    database_cursor.execute(sql, (user_id,))
    results = database_cursor.fetchall()
    alerts = []
    for alert in results :
        alerts.append("{} at {}".format(alert[0], alert[1]))

    return render_template('snippets/eventlog_snippet.html', messages = alerts)



@app.route('/videoframefetch/<frame>/<session>/<sensor>')
def videoframefetch(frame, session, sensor):
    #print(frame)
    #print(session)
    #print(sensor)
    # TODO: avoid doing queries during every frame fetch by supplying client-side with the paths/metadata
    frame = int(frame)
    sql = "SELECT * FROM VideoFrames WHERE SessionId = %s AND SensorId = %s AND %s BETWEEN FirstFrameNumber AND LastFrameNumber;"
    database_cursor = mysql.connection.cursor()
    database_cursor.execute(sql, (session, sensor, frame))

    frames_record = database_cursor.fetchone()
    last_frame_number = int(frames_record[4])
    frames_metadata = json.loads(frames_record[7])

    #number_of_frames_to_send = 10 if frame + 10 <= last_frame_number else (last_frame_number - frame) + 1
    number_of_frames_to_send = 1
    # TODO: remove absolute paths like this and do it dynamically
    base_path = "/root/data-ingester/"

    response_frames = []
    for frame_to_send in range(frame, frame + number_of_frames_to_send):
        frame_metadata = frames_metadata[str(frame_to_send)]
        path = base_path + frame_metadata['path']
        with open(path, 'rb') as frame_file:
            response_frames.append(frame_file.read() + b"FrameSeperator")
    
    response_bytes = b"".join(response_frames)
    #print(len(response_bytes))

    response = Response(
        response_bytes,
        200,
        mimetype='image/jpeg',
        direct_passthrough=True,
    )

    response.headers.add('Accept-Ranges', 'bytes')

    return response

@app.route('/audiosegmentfetch/<timestamp>/<segment>/<session>/<sensor>')
def audiosegmentfetch(timestamp, segment, session, sensor):
    #print(timestamp)
    #print(segment)
    #print(session)
    #print(sensor)
    database_cursor = mysql.connection.cursor()

    timestamp = int(timestamp)
    segment = int(segment)

    # fetch on segment number
    segments_record = []
    if timestamp == -1:
        sql = "SELECT * FROM AudioSegments WHERE SessionId = %s AND SensorId = %s AND %s BETWEEN FirstSegmentNumber AND LastSegmentNumber;"
        database_cursor.execute(sql, (session, sensor, segment))
        segments_record = database_cursor.fetchone()
    elif segment == -1:
        pass
    
    print(segments_record)
    segments_metadata = json.loads(segments_record[7])
    base_path = "/root/data-ingester/"
    segment_metadata = {}

    if timestamp == -1:
        # set timestamp in here
        segment_metadata = segments_metadata[str(segment)]
        timestamp = int(segment_metadata['time'])
    elif segment == -1:
        # set segment in here
        pass
    
    # TODO: refactor to store absolute path in db
    path = base_path + segment_metadata['path']

    response_bytes = bytes()
    with open(path, 'rb') as segment_file:
        response_bytes = segment_file.read()
    
    response = Response(
        response_bytes,
        200,
        mimetype='audio/mpeg',
        direct_passthrough=True,
    )

    response.headers.add('segment-number', segment)
    response.headers.add('segment-time', timestamp)

    return response


    # TODO: avoid doing queries during every frame fetch by supplying client-side with the paths/metadata
    frame = int(frame)
    sql = "SELECT * FROM VideoFrames WHERE SessionId = %s AND SensorId = %s AND %s BETWEEN FirstFrameNumber AND LastFrameNumber;"
    database_cursor.execute(sql, (session, sensor, frame))

    frames_record = database_cursor.fetchone()
    last_frame_number = int(frames_record[4])
    frames_metadata = json.loads(frames_record[7])

    #number_of_frames_to_send = 10 if frame + 10 <= last_frame_number else (last_frame_number - frame) + 1
    number_of_frames_to_send = 1
    # TODO: remove absolute paths like this and do it dynamically
    base_path = "/root/data-ingester/"

    response_frames = []
    for frame_to_send in range(frame, frame + number_of_frames_to_send):
        frame_metadata = frames_metadata[str(frame_to_send)]
        path = base_path + frame_metadata['path']
        with open(path, 'rb') as frame_file:
            response_frames.append(frame_file.read() + b"FrameSeperator")
    
    response_bytes = b"".join(response_frames)
    #print(len(response_bytes))

    response = Response(
        response_bytes,
        200,
        mimetype='image/jpeg',
        direct_passthrough=True,
    )

    response.headers.add('segment-number', segment)
    response.headers.add('segment-time', timestamp)

    return response


@app.route('/fetchvideo', methods=['GET'])
def fetchvideo():
    print("FETCHING VIDEO")
    path = "/root/capstone-site/site/static/video/107-6.mp4"
    #start, end = get_range(request)
    #return partial_response(path, start, end)
    return partial_response(path, 0, BUFF_SIZE, None)


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


def partial_response(path, start, buff_size, end=None):
    #LOG.info('Requested: %s, %s', start, end)
    file_size = os.path.getsize(path)

    # Determine (end, length)
    if end is None:
        end = start + buff_size - 1
    end = min(end, file_size - 1)
    end = min(end, start + buff_size - 1)
    end = file_size - 1
    length = end - start + 1

    # start is the bytes number requested by the browser
    # Read file
    with open(path, 'rb') as fd:
        fd.seek(start)
        bytes = fd.read(length)
    assert len(bytes) == length

    response = Response(
        bytes,
        200,
        mimetype=mimetypes.guess_type(path)[0],
        direct_passthrough=True,
    )
    response.headers.add(
        'Content-Range', 'bytes {0}-{1}/{2}'.format(
            #start, end, file_size,
            start, end, file_size,
        ),
    )
    response.headers.add(
        'Accept-Ranges', 'bytes'
    )
    #LOG.info('Response: %s', response)
    #LOG.info('Response: %s', response.headers)
    return response




"""route is used for downloading boilerplate code"""
@app.route('/downloadBoilerplate')
def downloadBoilerplate():
    return send_from_directory(directory=app.config['DOWNLOADS_FOLDER'], filename="boilerplate.py", as_attachment=True)


"""route is used to upload algorithms"""
@app.route("/algorithm_upload", methods=['GET', 'POST'])
def algorithm_upload():
    algorithms = []
    runningAlgorithms = []
    user_id = session.get('user_id')
    database_cursor = mysql.connection.cursor()

    if request.method == 'POST':
        # Checking that the POST request has the file part
        if 'file' not in request.files:
            return jsonify({'result' : 'No File Part'})

        file = request.files['file']

        # Checking that a file was selected
        if file.filename == '':
            return jsonify({'result' : 'No File Selected'})

        # Checking that the file name has an extension that is allowed
        if file and ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS):
            filename = secure_filename(file.filename)

            # Search Algorithm table for algorithm filename to get file path if it exists
            sql = """
                SELECT Path 
                FROM Algorithm 
                WHERE UserId = %s AND Name = %s;
            """
            database_cursor.execute(sql, (user_id, filename))
            path = database_cursor.fetchone()

            # If algorithm filename does not exist
            if path == None:
                # Get the path of the most recently uploaded algorithm pertaining to a user
                sql = """
                    SELECT Path
                    FROM Algorithm
                    WHERE UserId = %s
                    ORDER BY id DESC
                    LIMIT 1;
                """
                database_cursor.execute(sql, (user_id,))
                pth = database_cursor.fetchone()

                # If user has no recently uploaded algorithm
                if pth == None:
                    sql = """ 
                        INSERT INTO Algorithm
                        (UserId, Status, Name, Path)
                        VALUES (%s, %s, %s, %s);
                    """
                    database_cursor.execute(sql, (user_id, 0, filename, str(user_id) + "-1"))
                    mysql.connection.commit()

                    file.save(os.path.join(app.config['UPLOADS_FOLDER'], str(user_id) + "-1.py"))

                # If user has a recently uploaded algorithm
                else:
                    alg_num = int(pth[0].split('-')[1]) + 1
                    sql = """ 
                        INSERT INTO Algorithm
                        (UserId, Status, Name, Path)
                        VALUES (%s, %s, %s, %s);
                    """
                    database_cursor.execute(sql, (user_id, 0, filename, str(user_id) + "-" + str(alg_num)))
                    mysql.connection.commit()

                    file.save(os.path.join(app.config['UPLOADS_FOLDER'], str(user_id) + "-" + str(alg_num) + ".py"))
                
            # If algorithm filename exists
            else:
                # TODO: Algorithm overwrite prompt

                # Update the old file path
                file.save(os.path.join(app.config['UPLOADS_FOLDER'], path[0] + ".py"))

            # Search Algorithm table for filenames of algorithms pertaining to a user
            sql = """
                SELECT Status, Name 
                FROM Algorithm 
                WHERE UserId = %s;
            """            
            database_cursor.execute(sql, (user_id,))
            algs = database_cursor.fetchall()

            for alg in algs:
                if alg[0] == 1:
                    runningAlgorithms.append(alg[1])
                algorithms.append(alg[1])

            return render_template('snippets/uploads_list_snippet.html', algorithms = algorithms, runningAlgorithms = runningAlgorithms)

        return jsonify({'result' : 'File Extension Not Allowed'})

    elif request.method == 'GET':
        # Search Algorithm table for filenames of algorithms pertaining to a user
        sql = """
            SELECT Status, Name 
            FROM Algorithm 
            WHERE UserId = %s;
        """            
        database_cursor.execute(sql, (user_id,))
        algs = database_cursor.fetchall()

        for alg in algs:
            if alg[0] == 1:
                runningAlgorithms.append(alg[1])
            algorithms.append(alg[1])

        return render_template('snippets/uploads_list_snippet.html', algorithms = algorithms, runningAlgorithms = runningAlgorithms)


"""route is used to handle uploaded algorithms"""
@app.route('/algorithm_handler', methods=['POST'])
def algorithm_handler():
    algorithms = []
    runningAlgorithms = []
    database_cursor = mysql.connection.cursor()
    filename = request.form['filename'] + ".py"
    buttonPressed = request.form['button']
    user_id = session.get('user_id')

    if buttonPressed == "select":
        # Search Algorithm table for running algorithms pertaining to a user
        sql = """
            SELECT Name
            FROM Algorithm
            WHERE UserId = %s AND Status = 1;
        """
        database_cursor.execute(sql, (user_id,))
        algs = database_cursor.fetchall()

        for alg in algs:
            runningAlgorithms.append(alg[0])

        # Get the actual filename of the file to run
        sql = """
            SELECT Path
            FROM Algorithm
            WHERE UserId = %s AND Name = %s;
        """
        database_cursor.execute(sql, (user_id, filename))
        filename_actual = database_cursor.fetchone()[0] + ".py"

        if filename not in runningAlgorithms:
            program_thread = Program(Queue(), args=(True, filename_actual, user_id))
            program_thread.start()
           
            # Set Status of file to 1 for running
            sql = """
                UPDATE Algorithm
                SET Status = 1
                WHERE UserId = %s AND Name = %s;
            """
            database_cursor.execute(sql, (user_id, filename))
            mysql.connection.commit()

            # Search Algorithm table for filenames of algorithms pertaining to a user
            sql = """
                SELECT Status, Name 
                FROM Algorithm 
                WHERE UserId = %s;
            """            
            database_cursor.execute(sql, (user_id,))
            algs = database_cursor.fetchall()

            runningAlgs = []

            for alg in algs:
                if alg[0] == 1:
                    runningAlgs.append(alg[1])
                algorithms.append(alg[1])

            return render_template('snippets/uploads_list_snippet.html', algorithms = algorithms, runningAlgorithms = runningAlgs)

        else:       
            # Set Status of file to 0 for not running
            sql = """
                UPDATE Algorithm
                SET Status = 0
                WHERE UserId = %s AND Name = %s;
            """
            database_cursor.execute(sql, (user_id, filename))
            mysql.connection.commit()

            # Search Algorithm table for filenames of algorithms pertaining to a user
            sql = """
                SELECT Status, Name 
                FROM Algorithm 
                WHERE UserId = %s;
            """            
            database_cursor.execute(sql, (user_id,))
            algs = database_cursor.fetchall()

            runningAlgs = []

            for alg in algs:
                if alg[0] == 1:
                    runningAlgs.append(alg[1])
                algorithms.append(alg[1])

            return render_template('snippets/uploads_list_snippet.html', algorithms = algorithms, runningAlgorithms = runningAlgs)

    elif buttonPressed == "view":
        # Search Algorithm table for algorithm filename to get file path
        sql = """
            SELECT Path 
            FROM Algorithm 
            WHERE UserId = %s AND Name = %s;
        """
        database_cursor.execute(sql, (user_id, filename))
        path = database_cursor.fetchone()[0]

        f = open(os.path.join(app.config['UPLOADS_FOLDER'], path + ".py"), "r")
        content = f.read()
        f.close()
        return render_template('snippets/uploads_view_snippet.html', content = content, filename = filename)

    elif buttonPressed == "delete":
        # Search Algorithm table for algorithm filename to get file path
        sql = """
            SELECT Path 
            FROM Algorithm 
            WHERE UserId = %s AND Name = %s;
        """
        database_cursor.execute(sql, (user_id, filename))
        path = database_cursor.fetchone()[0]

        with os.scandir(os.path.join(app.config['UPLOADS_FOLDER'])) as entries:
            for entry in entries:
                if entry.is_file() and (entry.name == (path + ".py")):
                    os.remove(os.path.join(app.config['UPLOADS_FOLDER'], path + ".py"))

        sql = """
            DELETE FROM Algorithm
            WHERE UserId = %s AND Name = %s;
        """
        database_cursor.execute(sql, (user_id, filename))
        mysql.connection.commit()

        # Search Algorithm table for filenames of algorithms pertaining to a user
        sql = """
            SELECT Status, Name 
            FROM Algorithm 
            WHERE UserId = %s;
        """            
        database_cursor.execute(sql, (user_id,))
        algs = database_cursor.fetchall()

        runningAlgs = []

        for alg in algs:
            if alg[0] == 1:
                runningAlgs.append(alg[1])
            algorithms.append(alg[1])

        return render_template('snippets/uploads_list_snippet.html', algorithms = algorithms, runningAlgorithms = runningAlgs)
                    
    return jsonify({'result' : 'Button Not Handled'})