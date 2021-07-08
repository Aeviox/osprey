from app.main import socketio
from app import Database

from app.main.browser_events import log, update_pages

"""
Namespaces:
    UUID of stream: only that stream
    "/analyzers": all analyzer streams
    "/streamers": all streams (including analyzers)
"""


@socketio.on('connect', namespace='/streamers')
def connect():
    """ On disconnecting from a streamer """
    # print("A Streamer connected")
    pass


@socketio.on('disconnect', namespace='/streamers')
def disconnect():
    """ On disconnecting from a streamer """
    # print("A Streamer disconnected")
    pass


@socketio.on('init', namespace='/streamers')
def streamer_init(stream_id):
    """ notified when a new stream has been initialized """
    # tell waiting analyzers to check to see if this is their target
    socketio.emit('check_database', stream_id, namespace='/analyzers')


@socketio.on('update', namespace='/streamers')
def streamer_update(stream_id):
    """ notified that the streamer's info has updated """
    update_pages()


@socketio.on('log', namespace='/streamers')
def streamer_log(resp):
    """ On receiving logs from streamers, forward to the browser log """
    log(resp)