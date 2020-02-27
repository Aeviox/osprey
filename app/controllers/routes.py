import os
import urllib.request
import time
from flask import render_template, flash, redirect, url_for, Response, session, jsonify, request,Flask, session
from app import app
from app.controllers.forms import LoginForm, TriggerSettingsForm, RegistrationForm
from app.controllers.video import *
from app.controllers.data import *
from app import mysql
from app import bcrypt
from datetime import datetime
from random import seed, randint
from werkzeug.utils import secure_filename



ALLOWED_EXTENSIONS = set(['py'])

# Seed used to for random number generation
seed(1)


# Data structure for handling audio data
audioData = Audio()
# Data structure for handling temperature data
temperatureData = Temperature()
# Data structure for handling event log data
eventLogData = EventLog()
# Data structure for handling trigger settings form data
triggerSettingsFormData = TriggerSettingsFormData()

#check for if user has login into the website

# MySQL Insertion example
#cursor = db_connection.cursor()
#sql = "INSERT INTO `users` (`email`, `password`) VALUES (%s, %s)"
#cursor.execute(sql, ('devolde2@msu.edu', 'very-secret'))
#db_connection.commit()

#limit_visit_for one session
visit_limit = 15


@app.route('/', methods = ['GET','POST'])
@app.route('/login', methods=['GET', 'POST'])
def login():
    database_cursor = mysql.connection.cursor()
    database_cursor.execute('''CREATE TABLE IF NOT EXISTS visitor (id INTEGER UNSIGNED AUTO_INCREMENT PRIMARY KEY , ip VARCHAR(60),browser VARCHAR(60), time TEXT, page VARCHAR(60))''')
    database_cursor.execute('''CREATE TABLE IF NOT EXISTS bannedip (id INTEGER UNSIGNED AUTO_INCREMENT PRIMARY KEY , ip VARCHAR(60))''')
    database_cursor.execute('''CREATE TABLE IF NOT EXISTS visitortime (id INTEGER UNSIGNED AUTO_INCREMENT PRIMARY KEY , ip VARCHAR(60), visittime INT)''')
    form = LoginForm()
    print('ip: {}'.format(request.remote_addr))
    print('browser:{}'.format(request.user_agent.browser))
    print('date:{}'.format(time.strftime('%A %B, %d %Y %H:%M:%S')))

    ip = request.remote_addr
    browser = request.user_agent.browser
    date = time.strftime('%A %B, %d %Y %H:%M:%S')
    #check for visit
    visit_time_this_session = "SELECT * FROM visitortime where ip = %s"
    database_cursor.execute(visit_time_this_session,[ip])
    visit_result = database_cursor.fetchone()
    check_banned = "SELECT * FROM bannedip where ip = %s"
    database_cursor.execute(check_banned,[ip])
    banned_result= database_cursor.fetchone()
    if banned_result != None:
        return("You are banned by the website for too many request")
    if visit_result == None:
        new_visitor = "INSERT INTO visitortime (ip, visittime) VALUES (%s,%s)"
        database_cursor.execute(new_visitor,(ip,1))
    else:
        if visit_result[2] > visit_limit:
            banned_sql = "INSERT INTO bannedip (ip) VALUES (%s)"
            database_cursor.execute(banned_sql,[ip])
            return("You are banned by the website for too many request")
        else:
            timevisit = visit_result[2] + 1
            update_sql = "UPDATE visitortime SET visittime = %s WHERE ip = %s"
            database_cursor.execute(update_sql,(timevisit,ip))
            
   # print("visit_time_session:{}".format(visit_result))
    sql = "INSERT INTO `visitor` (ip,browser, time,page) VALUES (%s, %s, %s,%s)"
    database_cursor.execute(sql, (ip, browser, date, 'login'))
    
    mysql.connection.commit()

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
        print(myresult)
        if myresult == None:
            flash("UserName does not exist")
            redirect(url_for('login'))
            return render_template('login.html', title='Sign In', form=form)

        print("fadfasfs")
        hashed_pw_from_db = myresult[2]
        print("hashed data: {}".format(hashed_pw_from_db))
        user_input_pw = form.password.data
        correction_password = bcrypt.check_password_hash(hashed_pw_from_db, user_input_pw)
        print(correction_password)
       # print(session['user'])
        if correction_password == False:
            flash("Wrong password")
            redirect(url_for('login'))
            # return redirect(url_for('livefeed'))
        else:
            # flash("login password work")
            session['user'] = form.username.data
            return redirect(url_for('livefeed'))
    return render_template('login.html', title='Sign In', form=form)

@app.route('/logout')
def logout():
    # remove the username from the session if it is there
   try:session.pop('user', None)
   except:
       return redirect(url_for('login'))
   return redirect(url_for('login'))

@app.route('/banned')
def banned():
    # remove the username from the session if it is there
   return render_template('banned.html')

@app.route('/home')
def home():
    database_cursor = mysql.connection.cursor()
    ip = request.remote_addr
    browser = request.user_agent.browser
    date = time.strftime('%A %B, %d %Y %H:%M:%S')
        #check for visit
    visit_time_this_session = "SELECT * FROM visitortime where ip = %s"
    database_cursor.execute(visit_time_this_session,[ip])
    visit_result = database_cursor.fetchone()
    if visit_result == None:
        new_visitor = "INSERT INTO visitortime (ip, visittime) VALUES (%s,%s)"
        database_cursor.execute(new_visitor,(ip,1))
    else:
        if visit_result[2] > visit_limit:
            banned_sql = "INSERT INTO bannedip (ip) VALUES (%s)"
            database_cursor.execute(banned_sql,[ip])
            return("You are Banned from website for too many visit!")
        else:
            timevisit = visit_result[2] + 1
            update_sql = "UPDATE visitortime SET visittime = %s WHERE ip = %s"
            database_cursor.execute(update_sql,(timevisit,ip))
    sql = "INSERT INTO `visitor` (ip,browser, time,page) VALUES (%s, %s, %s,%s)"
    database_cursor.execute(sql, (ip, browser, date, 'home'))
    mysql.connection.commit()
    return render_template('home.html')


@app.route('/registration', methods=['GET', 'POST'])
def registration():
    database_cursor = mysql.connection.cursor()
    ip = request.remote_addr
    browser = request.user_agent.browser
    date = time.strftime('%A %B, %d %Y %H:%M:%S')
        #check for visit
    visit_time_this_session = "SELECT * FROM visitortime where ip = %s"
    database_cursor.execute(visit_time_this_session,[ip])
    visit_result = database_cursor.fetchone()
    if visit_result == None:
        new_visitor = "INSERT INTO visitortime (ip, visittime) VALUES (%s,%s)"
        database_cursor.execute(new_visitor,(ip,1))
    else:
        if visit_result[2] > visit_limit:
            banned_sql = "INSERT INTO bannedip (ip) VALUES (%s)"
            database_cursor.execute(banned_sql,[ip])
            return("You are Banned from website for too many visit!")
        else:
            timevisit = visit_result[2] + 1
            update_sql = "UPDATE visitortime SET visittime = %s WHERE ip = %s"
            database_cursor.execute(update_sql,(timevisit,ip))
    sql = "INSERT INTO `visitor` (ip,browser, time,page) VALUES (%s, %s, %s,%s)"
    database_cursor.execute(sql, (ip, browser, date, 'registration'))
    mysql.connection.commit()
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

        sql_checking_existance = "SELECT * FROM user WHERE username = %s"
        database_cursor.execute('''CREATE TABLE IF NOT EXISTS user (id INTEGER UNSIGNED AUTO_INCREMENT PRIMARY KEY , username VARCHAR(60), hashed_pw TEXT)''')
        database_cursor.execute(sql_checking_existance,[username])
        result = database_cursor.fetchall()
        print(result)
        if len(result) == 0:
            database_cursor.execute(" INSERT INTO user (username,hashed_pw) VALUES ("+ '"'+ username +'"' + ","+ '"'+pw_hash +'"' +")")
        else:
            flash("username has already been used")
            return render_template('registration.html', title='Registration', form=form)

        #INSERT INTO Persons (FirstName,LastName)
        #VALUES ('Lars','Monsn');
        # https://www.w3schools.com/python/python_mysql_select.asp

        mysql.connection.commit()

        return redirect(url_for('login'))
    return render_template('registration.html', title='Registration', form=form)


@app.route('/livefeed', methods=['GET', 'POST'])
def livefeed():

    if session.get('user') == None:
        return redirect(url_for('login'))
    else:
        flash("welcome! " + session.get('user'))
    database_cursor = mysql.connection.cursor()
    ip = request.remote_addr
    browser = request.user_agent.browser
    date = time.strftime('%A %B, %d %Y %H:%M:%S')
    #check for visit
    visit_time_this_session = "SELECT * FROM visitortime where ip = %s"
    database_cursor.execute(visit_time_this_session,[ip])
    visit_result = database_cursor.fetchone()
    if visit_result == None:
        new_visitor = "INSERT INTO visitortime (ip, visittime) VALUES (%s,%s)"
        database_cursor.execute(new_visitor,(ip,1))
    else:
        if visit_result[2] > visit_limit:
            banned_sql = "INSERT INTO bannedip (ip) VALUES (%s)"
            database_cursor.execute(banned_sql,[ip])
            return("You are Banned from website for too many visit!")
        else:
            timevisit = visit_result[2] + 1
            update_sql = "UPDATE visitortime SET visittime = %s WHERE ip = %s"
            database_cursor.execute(update_sql,(timevisit,ip))
    sql = "INSERT INTO `visitor` (ip,browser, time,page) VALUES (%s, %s, %s,%s)"
    database_cursor.execute(sql, (ip, browser, date, 'livefeed'))
    mysql.connection.commit()

    return render_template('livefeed.html', temperatureData = temperatureData, audioData = audioData)


@app.route('/archives')
def archives():

    #checking for user loggin
    if session.get('user') == None:
        return redirect(url_for('login'))
    database_cursor = mysql.connection.cursor()
    ip = request.remote_addr
    browser = request.user_agent.browser
    date = time.strftime('%A %B, %d %Y %H:%M:%S')
        #check for visit
    visit_time_this_session = "SELECT * FROM visitortime where ip = %s"
    database_cursor.execute(visit_time_this_session,[ip])
    visit_result = database_cursor.fetchone()
    if visit_result == None:
        new_visitor = "INSERT INTO visitortime (ip, visittime) VALUES (%s,%s)"
        database_cursor.execute(new_visitor,(ip,1))
    else:
        if visit_result[2] > visit_limit:
            banned_sql = "INSERT INTO bannedip (ip) VALUES (%s)"
            database_cursor.execute(banned_sql,[ip])
            return("You are Banned from website for too many visit!")
        else:
            timevisit = visit_result[2] + 1
            update_sql = "UPDATE visitortime SET visittime = %s WHERE ip = %s"
            database_cursor.execute(update_sql,(timevisit,ip))
    sql = "INSERT INTO `visitor` (ip,browser, time,page) VALUES (%s, %s, %s,%s)"
    database_cursor.execute(sql, (ip, browser, date, 'archives'))
    mysql.connection.commit()
    return archive(None)


@app.route('/archive/<int:archive_id>')
def archive(archive_id):
    #checking for user login
    if session.get('user') == None:
        return redirect(url_for('login'))
    if archive_id == None:
        print("Archive id is None")
    else:
        print("archive_id: ", archive_id)

    
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

    return render_template('snippets/eventlog_snippet.html', messages = alerts)


"""route is used to update the event log with audio data"""
@app.route('/update_eventlog_audio', methods=['GET', 'POST'])
def update_eventlog_audio():
    alerts = []

    eventLogData.audioStatus = request.form['status']

    if (eventLogData.audioStatus == 'ON' and audioData.status == 'ON'):
        #print('ALL AUDIO ON')
        if (audioData.decibels > triggerSettingsFormData.audio):
            alerts.append("Audio Trigger: Audio exceeded " + triggerSettingsFormData.audio + " dB @ " + audioData.date)

    return render_template('snippets/eventlog_snippet.html', messages = alerts)


"""route is used to upload files"""
@app.route("/file_upload", methods=['GET', 'POST'])
def file_upload():
    files = []

    if request.method == 'POST':
        # Checking that the post request has the file part
        if 'file' not in request.files:
            return jsonify({'result' : 'No File Part'})

        file = request.files['file']

        # Checking that a file was selected
        if file.filename == '':
            return jsonify({'result' : 'No File Selected'})

        # Ensuring that the file name has an extension that is allowed
        if file and ('.' in file.filename and file.filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOADS_FOLDER'], filename))
            # Getting a list of all files in the uploads directory
            with os.scandir(app.config['UPLOADS_FOLDER']) as entries:
                for entry in entries:
                    if entry.is_file():
                        files.append(entry.name)
            return render_template('snippets/uploads_list_snippet.html', files = files)

        return jsonify({'result' : 'File Extension Not Allowed'})

    if request.method == 'GET':
        # Getting a list of all files in the uploads directory
        with os.scandir(app.config['UPLOADS_FOLDER']) as entries:
            for entry in entries:
                if entry.is_file():
                    files.append(entry.name)
        return render_template('snippets/uploads_list_snippet.html', files = files)
