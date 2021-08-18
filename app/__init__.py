import os

#from eventlet import monkey_patch
#monkey_patch()

from flask import Flask, send_from_directory
from flask_session import Session
from redis import from_url

from lib.database import DatabaseController
from server.interface import interface  # import the customized interface object


def create_app():
    """ Application factory to create the app and be passed to workers """
    app = Flask(__name__)

    app.config['SECRET_KEY'] = 'thisisthesecretkeyfortheflaskserver'
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_REDIS'] = from_url('redis://localhost:6379')
    app.config['UPLOAD_FOLDER'] = 'local/pipelines'
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1000 * 1000  # 16 MB
    Session(app)  # initialize server side session

    # interface to database connections
    app.database_controller = DatabaseController(live_path='data/live', saved_path='data/saved')
    app.interface = interface  # allow the app to access to the customized interface object

    '''
    # add basic favicon
    @app.route('/favicon.ico')
    def favicon():
        return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

    # serve static js files
    @app.route('/js/<filename>')
    def serve_js(filename):
        return send_from_directory(os.path.join(app.root_path, 'static', 'js'), filename)
    '''

    # register blueprints and sockets
    from app.main import auth
    app.register_blueprint(auth)

    from app.main import streams
    app.register_blueprint(streams)
    app.add_url_rule('/', endpoint='index')

    from app.main import socketio
    socketio.init_app(app, async_mode='eventlet', manage_session=False, cors_allowed_origins="https://signalstream.org")
    # manage_sessions=False means that the socketIO and HTTP sessions will be the same
    # cors_allowed_origins allows socketio to work with SSL
    return app


