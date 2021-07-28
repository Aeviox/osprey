from flask import current_app, session, request

from re import match
from functools import wraps
from traceback import print_exc

from os import listdir, system
from os.path import isfile, join

from app.main import socketio
from lib.database import DatabaseError, DatabaseTimeoutError, DatabaseBusyLoadingError, DatabaseConnectionError


def log(msg, level=0, everywhere=False):
    """
    Log a message in the browser.
    Level: 0=log, 1=info, 2=warn, 3=error
    If everywhere is True, broadcast to ALL browsers, even in different sessions.
    """
    msg = str(msg)  # if an exception, convert to string
    data = {'level': level, 'message': msg}
    if everywhere:
        socketio.emit('log', data, namespace='/browser')
    else:
        socketio.emit('log', data, namespace='/browser', room=request.sid)

    if level in [0, 1]:
        return
    elif level == 2:
        pre = "[WARN]"
    else:
        pre = "[ERROR]"
    print("{}: {}".format(pre, msg))


def info(msg, everywhere=False):
    """ Log an info message in the browser """
    log(msg, level=1, everywhere=everywhere)  # log level 1


def warn(msg, everywhere=False):
    """ Log a warning message in the browser """
    log(msg, level=2, everywhere=everywhere)  # log level 2


def error(msg, everywhere=False):
    """ Log an error message in the browser """
    log(msg, level=3, everywhere=everywhere)  # log level 3


def catch_errors(handler):
    """ Decorator to catch and send error messages to the browser """
    @wraps(handler)
    def wrapped_handler(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except DatabaseBusyLoadingError:
            error("Database is still loading into memory - try again in a bit")
        except DatabaseTimeoutError:
            error("Database operation timed out - try again in a bit")
        except DatabaseConnectionError:
            error("Lost connection to database")
        except DatabaseError as e:
            error("Database operation failed: {}".format(e))
        except Exception as e:
            error("Server error in {}(): {}: {}".format(handler.__name__, e.__class__.__name__, e))
            print_exc()
    return wrapped_handler


def set_database(file=None):
    """
    Sets the current session database.
    If no file is given, sets it to a live database connection.
    If a file is given, sets it to a playback database connection to that file.
    """
    sid = session.sid  # current session ID
    ctrl = current_app.database_controller

    ctrl.remove(sid)  # remove current database
    if file:
        ctrl.new_playback(file=file, ID=session.sid)
        log("")
    else:
        ctrl.new_live(ID=session.sid)

    database = ctrl.get(session.sid)
    print("SET DATABASE:", database)

    return database


def get_database():
    """ Retrieves the Database object from the current session """
    database = current_app.database_controller.get(session.sid)
    return database


def remove_database():
    """ Removes current database in session """
    current_app.database_controller.remove(session.sid)


def check_filename(file):
    """ validated syntax of file name """
    if not match(r"^[0-9a-zA-Z_:\-.]+$", file):
        raise Exception("Invalid file name. May only contain digits, letters, underscore, hyphen, and period.")


@catch_errors
def update_pages(room=None):
    """
    Updates list of connected streams in browser.
    If room is given, send update to that room.
    If not, send to only the current request.
    """
    database = get_database()
    if not database:
        return  # no database connection - do nothing

    try:  # attempt to read list of group names
        groups = database.get_all_groups()
    except DatabaseError as e:
        error("Error retrieving streams from database: {}".format(e))
        groups = []
    if groups is None:
        error("Tried to retrieve list of pages, got None")
        return

    if not room:  # if room not given, send to room ID of current request
        room = request.sid
    socketio.emit('update_pages', groups, namespace='/browser', room=room)


@catch_errors
def update_files(room=None):
    """
    Updates list of database files in browser.
    If room is given, send update to that room.
    If not, send to only the current request.
    """
    data_path = 'data/saved'
    files = []
    for file in listdir(data_path):
        # if a valid file and doesn't start with a period (hidden files)
        if isfile(join(data_path, file)) and not file.startswith('.'):
            files.append(file)

    if not room:  # if room not given, send to room ID of current request
        room = request.sid
    socketio.emit('update_files', files, namespace='/browser', room=room)


def set_button(name, hidden=None, disabled=None, text=None):
    """
    Updates the state of a single button in the current session.
    <name>: class name of the button to be targeted.
    <hidden>: whether the button is hidden.
    <disabled>: whether the button is disabled.
    <text>: button text, if changed.
    """
    if not session.get('buttons'):
        session['buttons'] = {}
    session['buttons'][name] = {'hidden': hidden, 'disabled': disabled, 'text': text}
    session.modified = True


@catch_errors
def update_buttons(room=None):
    """
    Sends all button data stored in session.
    If room is given, send update to that room.
    If not, send to only the current request.
    """
    if not session.get('buttons'):
        session['buttons'] = {}

    if not room:  # if room not given, send to room ID of current request
        room = request.sid
    socketio.emit('update_buttons', session['buttons'], namespace='/browser', room=room)
    print("SENT BUTTONS: ", session['buttons'])

