from multiprocessing import Lock
from time import time, sleep, strftime, localtime
import functools
import json
from os import system, path
from traceback import print_exc, print_stack
from numpy import ndarray

import redis
#from redistimeseries.client import Client as RedisTS

from datetime import timedelta

def h(ms):
    """ temporary for testing """
    if type(ms) == str:
        ms = float(ms.split('-')[0])
    return timedelta(milliseconds=ms)


class DatabaseError(Exception):
    """ invoked when the connection fails when performing a read/write operation """
    pass


class DatabaseConnectionError(DatabaseError):
    """ Invoked when the database refuses the connection or disconnects for some reaosn """


class DatabaseTimeoutError(DatabaseError):
    """
    Invoked when database is operation times out - the database may be busy or unresponsive
    """


class DatabaseBusyLoadingError(DatabaseError):
    """
    Invoked when the database is currently loading a file into memory.
    Essentially just used with a redis.exceptions.BusyLoadingError.
    """


def get_time_filename():
    """ Return human readable time for file names """
    return strftime("%Y-%m-%d_%H:%M:%S.rdb", localtime())


def catch_database_errors(method):
    """
    Method wrapper to catch database errors.
    Turns errors into a DatabaseError classes that can be caught elsewhere
    """
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        try:  # attempt to perform database operation
            return method(self, *args, **kwargs)
        except redis.exceptions.BusyLoadingError:
            raise DatabaseBusyLoadingError("Redis is loading the database into memory. Try again later.")
        except (redis.exceptions.TimeoutError, TimeoutError) as e:
            raise DatabaseTimeoutError("Database operation timed out")
        except (ConnectionResetError, ConnectionRefusedError, redis.exceptions.ConnectionError) as e:
            raise DatabaseConnectionError("{}: {}".format(e.__class__.__name__, e))
        except (redis.exceptions.ResponseError) as e:
            raise DatabaseError("Database Response Error: {}".format(e))
        except Exception as e:  # other type of error
            print_stack()
            print_exc()
            raise DatabaseError('Uncaught Error in Database ({}). {}: {}'.format(method.__name__, e.__class__.__name__, e))
    return wrapped


class DatabaseController:
    """ Handles connections to multiple different Database instances """
    def __init__(self, live_path, saved_path):
        # index of Database objects
        # keys are ID numbers for each database
        self.sessions = {}

        self.live_ip = '3.131.117.61'
        self.live_port = 5001
        self.live_path = live_path  # directory of live database dump files
        self.live_file = 'live.rdb'
        self.live_pass = 'thisisthepasswordtotheredisserver'

        # index of ports used for playback files and how many Database object connected
        self.playback_ports = {port: {'file': None, 'count': 0} for port in [7000, 7001, 7002]}
        self.playback_ip = '127.0.0.1'
        self.playback_pass = 'thisisthepasswordtotheredisplaybackserver'  # todo: just randomize this I guess
        self.save_path = saved_path

    def new_live(self, ID):
        """ Creates and returns a new liveDatabase instance for the given ID key """
        if self.sessions.get(ID):  # Database already associated
            self.remove(ID)  # remove and disconnect
        self.sessions[ID] = LiveDatabase(
            ip=self.live_ip, port=self.live_port, password=self.live_pass,
            file=self.live_file, live_path=self.live_path, save_path=self.save_path
        )

    def new_playback(self, file, ID):
        """ Creates and returns a new PlaybackDatabase instance for the given ID key """
        # iterate through index of ports
        port = None
        for p, info in self.playback_ports.items():
            if not info['file']:  # this port is available
                port = p
            elif info['file'] == file:  # the port is already associated with the given file
                port = p
                print("FOUND A PORT ALREADY ASSOCIATED FOR PLAYBACK: {}".format(p))
                break  # done

        else:  # no port was associated with this file already
            if port is None:  # no ports available
                raise DatabaseError("Could not create new playback server - no ports available")
            else:  # a port is available
                self.start_playback_server(file=file, port=port)

        self.playback_ports[port]['file'] = file  # set the file for this port if not already
        self.playback_ports[port]['count'] += 1  # increment count of Database classes connecting to this port

        if self.playback_ports[port]['count'] <= 0:
            raise DatabaseError("Error creating playback database - number of redis instances for port {} was negative".format(port))

        if self.sessions.get(ID):  # Database already associated
            self.remove(ID)  # remove and disconnect

        self.sessions[ID] = PlaybackDatabase(
            ip=self.playback_ip, port=port, password=self.playback_pass, file=file
        )

        count = self.playback_ports[port]['count']

    def get(self, ID):
        """ Get database by ID """
        return self.sessions.get(ID)

    def remove(self, ID):
        """ Disconnect a Database class and remove it from the dictionary """
        if self.sessions.get(ID):
            db = self.sessions[ID]

            # live database
            if db.live:
                return

            # playback database
            info = self.playback_ports[db.port]
            if not info:
                return
            if info['file'] and info['count'] > 1:  # still some left
                self.playback_ports[db.port]['count'] -= 1  # decrement

            # if a no more databases using this port
            elif info['file'] and info['count'] <= 1:
                db.shutdown()  # shut down this redis server
                self.playback_ports[db.port]['count'] = 0
                self.playback_ports[db.port]['file'] = None  # un-associate file from port

            del self.sessions[ID]  # remove from session index

    def rename_save(self, filename, newname):
        """ renames an old save file """
        if not filename:
            raise Exception("Could not rename file - no file given to rename")
        if not newname:
            newname = get_time_filename()
        if not newname.endswith('.rdb'):
            newname += '.rdb'
        if path.isfile(self.save_path + '/' + newname):
            raise Exception("Could not rename file - new file name already exists")
        if not path.isfile(self.save_path + '/' + filename):
            raise Exception("Could not rename file - file does not exist")
        try:
            system("mv {0}/{1} {0}/{2}".format(self.save_path, filename, newname))
        except Exception as e:
            raise DatabaseError("Failed to rename file: {}".format(e))

    def delete_save(self, filename):
        """ Remove an old stored file """
        if not filename:
            return
        if not path.isfile(self.save_path+'/'+filename):
            raise Exception("Could not delete file - file does not exist")
        try:
            system('rm {}/{}'.format(self.save_path, filename))
        except Exception as e:
            raise DatabaseError("Failed to delete file: {}".format(e))

    def start_playback_server(self, file, port):
        """ Start a new local Redis server instance initialized from <file> on port <port> """
        system("redis-server --bind 127.0.0.1 --daemonize yes --dir {} --dbfilename {} --port {} --requirepass {}".format(self.save_path, file, port, self.playback_pass))


class Database:
    """
    Wrapper class to handle a single connection to a database.
    May be created on its own - intended for use on remove device writing data.
    Note that redis timestamps are approximate - they will be accurate to a millisecond,
        which is enough for visual inspection. However the 'time' data column is the
        measurement-accurate timestamp for a given data point.
    """
    def __init__(self, ip, port, password):
        self.ip = ip  # ip of database
        self.port = port  # database port
        self.password = password  # database password

        # options for the redis.ConnectionPool
        options = {
            'host': ip,
            'port': port,
            'password': password,
            'socket_timeout': 2,
            'socket_connect_timeout': 5
        }

        # Separate redis pools for reading decoded data or reading raw bytes data.
        pool = redis.ConnectionPool(decode_responses=True, **options)
        bytes_pool = redis.ConnectionPool(decode_responses=False, **options)

        # Redis connection client
        self.redis = redis.Redis(connection_pool=pool)
        self.bytes_redis = redis.Redis(connection_pool=bytes_pool)

        # Create RedisTimeSeries Client instances as well
        # (wrapper around Redis instance to implement RedisTimeSeries commands)
        #self.redis_ts = RedisTS(conn=self.redis)
        #self.bytes_redis_ts = RedisTS(conn=self.bytes_redis)
        # todo: figure out how to convert to using RedisTimeSeries?

        self.exit = False  # flag to determine when to stop running if looping
        self.start_time = time()*1000  # real time that streaming is started (ms)

        # Index to track last read/write position in the database for each data column
        self.bookmarks = Bookmarks()

    def time(self):
        """ returns the current time in milliseconds """
        return time()*1000

    def time_to_redis(self, unix_time):
        """ Convert unix time (ms) to a redis timestamp (ms). Ignores precision beyond ms"""
        return str(unix_time).split('.')[0]

    def redis_to_time(self, redis_time):
        """ Convert redis time stand to unix time stamp (ms). Ignores precision beyond ms """
        if type(redis_time) == str:
            return float(redis_time.split('-')[0])
        elif type(redis_time) == bytes:
            return float(redis_time.split(b'-')[0])

    def validate_redis_time(self, redis_id, stream):
        """
        Validates a redis timestamp generated by time_to_redis()
        to make sure that no two timestamps written are the same
        """
        bookmark = self.bookmarks.get(stream)
        last_write = bookmark.write
        if not last_write:  # hasn't written before
            return redis_id

        bookmark.write = int(redis_id)  # set new last write
        if int(redis_id) == last_write:  # same integer millisecond
            redis_id = redis_id + '-' + str(bookmark.seq+1)
            bookmark.seq += 1  # incremenet sequence number
        else:  # different millisecond
            bookmark.seq = 0  # reset
        return redis_id

    @catch_database_errors
    def ping(self):
        """
        Ping database to ensure connecting is functioning
        Meant to be used to catch different database exception classes
        """
        if self.redis.ping():
            return True
        return False

    def is_streaming(self):
        """ Checks the database 'STREAMING" key. Does not propagate exceptions. """
        try:
            if self.redis.get('STREAMING'):
                return True
        except Exception as e:
            return False
        return False

    def valid_list(self, data):
        """ Checks if the given data is in a valid 'listy' format for ordered data """
        # todo: can this be more robust? Other possible formats?
        #  I tried to use "hasattr(type(data), '__iter__')" but that
        #     returns True for strings and byte arrays.
        if isinstance(data, (list, ndarray, tuple)):
            return True
        return False

    def decode(self, data):
        """ Decodes data if necessary """
        if type(data) == bytes:
            return data.decode('utf-8')
        return data

    @catch_database_errors
    def get_elapsed_time(self, stream):
        """ Gets the current length of time that a database has been playing for in seconds """
        bookmark = self.bookmarks[stream]
        if not bookmark:
            return 0

        start_time = bookmark.first_time
        if not start_time:
            return 0

        current_time = bookmark.last_time
        if not current_time:
            return 0

        return (current_time - start_time)/1000  # ms to s

    @catch_database_errors
    def write_data(self, stream, data):
        """
        Writes time series <data> to stream:<stream>.
        If <data> is a dictionary of items where keys are column names.
        Items must either all be iterable or all non-iterable.
        All Items (if iterable) must be of same length.
        Must include a 'time' column with unix time stamps in milliseconds.
        """
        if data.get('time') is None:  # check for time key
            raise DatabaseError("Data input dictionary must contain a 'time' key.")

        # make sure all values are the same size
        sizes = set()
        for val in data.values():
            if self.valid_list(val):  # iterable
                sizes.add(len(val))
            else:  # non-iterable
                sizes.add(None)
        if len(sizes) > 1:  # more than one size
            raise DatabaseError("Data input columns are not all the same size. Found sizes: {}".format(sizes))
        elif len(sizes) == 0:  # no data?
            raise DatabaseError("Data input contained no data columns? : {}".format(data))

        if self.valid_list(list(data.values())[0]):  # if an iterable sequence of data points
            pipe = self.redis.pipeline()  # pipeline queues a series of commands at once
            length = len(list(data.values())[0])  # length of data (all must be the same)

            # add data to the Redis database one data point at a time
            #  because there isn't a mass-insert-to-stream command
            ids = []
            for i in range(length):
                d = {}
                for key in data.keys():
                    d[key] = data[key][i]
                time_id = self.time_to_redis(data['time'][i])  # redis time stamp in which to insert
                redis_id = self.validate_redis_time(time_id, stream)
                pipe.xadd('stream:'+stream, d, id=redis_id)

            pipe.execute()
        else:  # assume this is a single data point
            time_id = self.time_to_redis(data['time'])  # redis time stamp in which to insert
            redis_id = self.validate_redis_time(time_id, stream)
            self.redis.xadd('stream:'+stream, {key: data[key] for key in data.keys()}, id=redis_id)

    @catch_database_errors
    def read_data(self, stream, count=None, max_time=None, numerical=True, to_json=False, decode=True, downsample=False):
        """
        Gets newest data for <reader> from data column <stream>.
        <stream> is some ID that identifies the stream in the database.
        <reader> is some ID that will keep track of it's own read head position.
        <count> is the number of data points to read (ignoring whether the point have already been read.
            - If None, read as many new points as possible.
            - If not None, ignores <max_time> and <reader>
        <max_time> maximum time window (s) to read. (If count is None).
            - If None, read as much as possible (guarantees all data read)
        <numerical>  Whether the data needs to be converted python float type
        <decode> Whether to decode the result into strings.
            If False, only values will remain as bytes. Keys will still be decoded.
        <to_json> whether to convert to json string. if False, uses dictionary of lists.
        """
        # TODO: downsampling for live data (see downsampling implemented for playback data)
        if stream is None:
            return

        # get bookmark for this stream, create if not exist
        bookmark = self.bookmarks.get(stream)
        if bookmark.lock(block=False):  # acquire lock
            return  # return if already locked

        if decode:
            red = self.redis
        else:
            numerical = False
            red = self.bytes_redis

        if count:  # get COUNT data regardless of last read
            response = red.xrevrange('stream:'+stream, count=count)

        else:
            if bookmark.last_id and bookmark.last_time:  # last read spot exists
                last_read_id = bookmark.last_id
                last_read_time = bookmark.last_time
                time_since_last = self.time()-last_read_time  # time since last read (ms)

                # if time since last read is greater than maximum, increment last read ID by the difference
                if max_time and time_since_last > max_time*1000:  # max_time is in seconds
                    new_id = self.redis_to_time(last_read_id) + (time_since_last-max_time*1000)
                    last_read_id = self.time_to_redis(new_id)  # convert back to redis timestamp

                response = red.xread({'stream:' + stream: last_read_id})
            else:  # no last read spot exists.
                response = red.xread({'stream:' + stream: '$'}, block=1000)  # read new data only

        if not response:
            bookmark.release()  # release lock
            return None

        # If count is given XRANGE is used, which gives a list of data points
        #   this means that the data is stored in response.
        # If no count is given, XREAD is used, which gives a a list of tuples
        #   (one for each stream read from), each of which contains a list of data points. But since
        #   we are only reading from one stream, the data is stored in response[0][1].
        if not count:
            response = response[0][1]

        # set first-read info if not already set
        if not bookmark.first_time:
            bookmark.first_time = self.start_time  # get first time
            bookmark.first_id = self.decode(response[-1][0])  # get first ID

        # set last-read info
        bookmark.last_time = self.time()
        bookmark.last_id = self.decode(response[-1][0])  # store last timestamp

        # create final output dict
        output = {}

        # loop through stream data and convert if necessart

        # I hate this
        if numerical and decode:
            for data in response:
                d = data[1]  # data dict. data[0] is the timestamp ID
                for key in d.keys():
                    if output.get(key):
                        output[key].append(float(d[key]))  # convert to float and append
                    else:
                        output[key] = [float(d[key])]

        elif numerical and not decode:
            for data in response:
                d = data[1]  # data dict. data[0] is the timestamp ID
                for key in d.keys():
                    k = key.decode('utf-8')  # key won't be decoded, but it needs to be
                    if output.get(k):
                        output[k].append(float(d[key]))  # convert to float and append
                    else:
                        output[k] = [float(d[key])]

        elif not numerical and decode:
            for data in response:
                # data[0] is the timestamp ID
                d = data[1]  # data dict
                for key in d.keys():
                    if output.get(key):
                        output[key].append(d[key])  # append
                    else:
                        output[key] = [d[key]]

        elif not numerical and not decode:
            for data in response:
                # data[0] is the timestamp ID
                d = data[1]  # data dict
                for key in d.keys():
                    k = self.decode(key)  # key won't be decoded, but it needs to be
                    if output.get(k):
                        output[k].append(d[key])  # append
                    else:
                        output[k] = [d[key]]

        if to_json:
            result = json.dumps(output)
        else:
            result = output

        bookmark.release()  # release lock
        return result

    @catch_database_errors
    def write_snapshot(self, stream, data):
        """
        Writes a snapshot of data <data> to stream:<stream>.
        <data> must be a dictionary of lists, where keys are data column names.
        Note that this method is for data which is not consecutive (like time series would be).
        It is for data that is meant to be viewed a chunk at a time.
        It places each list of data values as a comma separated list under one key.
        Must include a 'time' column with a single unix timestamp in milliseconds
        """
        if data.get('time') is None:  # check for time key
            raise DatabaseError("Data input dictionary must contain a 'time' key.")
        if type(data['time']) not in [int, float]:
            raise DatabaseError("Time of this snapshot must be an integer or float.")

        new_data = {}
        for key in data.keys():
            if key == 'time':
                new_data['time'] = data['time']
            else:
                new_data[key] = ','.join(str(val) for val in data[key])

        time_id = self.time_to_redis(data['time'])  # redis time stamp in which to insert
        redis_id = self.validate_redis_time(time_id, stream)

        self.redis.xadd('stream:' + stream, new_data, id=redis_id)

    @catch_database_errors
    def read_snapshot(self, stream, to_json=False, decode=True):
        """
        Gets latest snapshot for <reader> from data column <stream>. Only gets 1 data point.
        <stream> is some ID that identifies the stream in the database.
        <to_json> whether to convert to json string. if False, uses dictionary of lists.
            - Also note that this removes the 'time' data column. This is for
                plotting purposes - plotting software requires that all columns
                be of same length, and the time column only has one entry.
        Since this is a snapshot (not time series), gets last 1 data point from redis.
        """
        if not stream:
            return

        # get bookmark for this stream. Create a new one if it doesn't exist.
        bookmark = self.bookmarks.get(stream)
        if bookmark.lock(block=False):  # acquire lock
            return  # return if already locked

        if decode:
            red = self.redis
        else:
            red = self.bytes_redis  # not implemented for this method

        # read most recent snapshot - don't care about last read position
        response = red.xrevrange('stream:'+stream, count=1)
        if not response:
            bookmark.release()  # release lock
            return None

        # set first-read info if not already set
        # still necessary for calculating elapsed time
        if not bookmark.first_time:
            bookmark.first_time = self.start_time  # get first time
            bookmark.first_id = self.decode(response[0][0])  # get first ID

        # set last-read info
        bookmark.last_time = self.time()
        bookmark.last_id = self.decode(response[0][0])  # store last timestamp

        data = response[0][1]  # data dict
        keys = data.keys()  # get keys from data dict
        output = {key: [] for key in keys}

        for key in keys:
            vals = data[key].split(',')
            output[key] = [float(val) for val in vals]

        if to_json:
            del output['time']  # remove time column for json format
            result = json.dumps(output)
        else:
            result = output
        bookmark.release()  # release lock
        return result

    @catch_database_errors
    def set_info(self, key, data):
        """
        Writes <data> to info:<key>
        <data> must be a dictionary of key-value pairs.
        <key> is the key for this data set
        """
        self.redis.hmset('info:'+key, data)

    @catch_database_errors
    def get_info(self, ID, name=None):
        """
        Reads <name> from map with key info:<ID>
        if <name> not specified, gives dictionary with all key value pairs
        """
        if name is not None:
            return self.redis.hget('info:'+ID, name)
        else:
            data = self.redis.hgetall('info:' + ID)
            return data

    @catch_database_errors
    def get_all_info(self):
        """ Gets a list of dictionaries containing info for all connected streams """
        info = []
        for key in self.redis.execute_command('keys info:*'):
            info.append(self.redis.hgetall(key))
        return info

    @catch_database_errors
    def set_group(self, key, data):
        """
        Writes <data> to group:<key>
        <data> must be a dictionary of key-value pairs.
        <key> is the key for this data set
        """
        self.redis.hmset('group:'+key, data)

    @catch_database_errors
    def get_group(self, name, stream=None):
        """
        Gets an info dict from stream with name <stream> in group_name <name>
        if <name> not specified, gives list of all dicts in that group.
            - Note that this only returns one dict for each 'info:' data column,
                so any extra 'stream:' columns with the same but a different prefix
                do not add another dict.
        """
        if stream is not None:  # stream name specified
            stream_id = self.redis.hget('group:'+name, stream)  # get stream ID from group dict
            return self.get_info(stream_id)  # return dict for that stream

        else:  # no stream name specified - get whole group
            data = {}  # name: {stream info dict}
            group = self.redis.hgetall('group:'+name)  # name:ID
            for key in group.keys():  # for each stream name
                info = self.get_info(group[key])
                if info:
                    data[key] = info
            return data

    @catch_database_errors
    def get_all_groups(self):
        """ Gets a list of dictionaries containing name and ID info for all connected streams """
        info = []
        for key in self.redis.execute_command('keys group:*'):
            info.append(self.redis.hgetall(key))
        return info

    @catch_database_errors
    def get_streams(self, group):
        """
        Return a dictionary of all streams in a group
        This returns all 'stream:' columns in a group,
            including duplicated with different prefixes.
        """
        # todo: because I need a way to consistently find streams with prefixes,
        #       the method for creating them should be standardized and unchangeable.
        #  The best way to do this would be to hide their structure from the user.
        #  Right now, the user could technically write data to any stream name with
        #       any structure in the loop method of an Analyzer or streamer.
        #  To fix this, there should be a given method to "name" a stream, but under the hood
        #       always add the stream ID to it.
        #  The trouble then is how to retrieve that full ID in the create_layout() method.
        #  I think that whole thing should be redone - maybe as a class method that must be inherited?
        #  .
        #  For the purposes of this method, the structure is assumed to be:
        #  stream:prefix:full_stream_id
        data = {}  # name: ID
        group = self.redis.hgetall('group:'+group)  # name:ID
        for key, ID in group.items():
            # get all streams from this ID with an extra prefix
            extra_ids = self.redis.keys("stream:*{}".format(ID))
            for extra in extra_ids:
                i = extra.find(":")+1  # first colon
                j = extra[i:].find(":")  # second colon
                if j == -1:  # no second colon (thus no prefix)
                    name = key
                else:
                    name = key+':'+extra[i:i+j]
                data[name] = extra[i:]
        return data


class ServerDatabase(Database):
    """
    Base class to handle a connection to a database on the server where the database is hosted.
    Shouldn't be used or created directly.
    """
    def __init__(self, ip, port, password, file):
        super().__init__(ip, port, password)
        self._file = file

    @property
    def file(self):
        return self._file

    def kill(self):
        """ Manually kills the redis process by stopping activity on the port it's using """
        system("sudo fuser -k {}/tcp".format(self.port))


class LiveDatabase(ServerDatabase):
    """
    Database class used for a live connection on the server.
    Shares many of the same methods as Database, but adds
        functionality to control the state of the database.
    Created only by DatabaseController.new_live()
    """
    def __init__(self, ip, port, password, file, live_path, save_path):
        super().__init__(ip, port, password, file)
        self.live_path = live_path  # path to live directory
        self.save_path = save_path  # path to save directory
        self.start_time = self.get_start_time()  # get start time from database
        # todo: if two sessions are viewing the same live database, and one session
        #  stops and restarts the streams, the other session does not update its own
        #  self.start_time. I'm not fixing this now because of my idea about
        #  database connection rooms. See the comment in video_stream_events.py

    def __repr__(self):
        return "Live, {}:{}, {}".format(self.ip, self.port, self.file)

    @property
    def live(self):
        return True

    @catch_database_errors
    def start(self):
        """
        Live mode: Sets "STREAMING" key in database.
        Playback mode: Starts playback.
        """
        self.start_time = self.get_start_time()
        if not self.start_time:  # no start time set - not yet streaming
            self.start_time = self.time()  # mark streaming start time
            self.redis.set('START_TIME', self.start_time)  # set start time to be read by others

        self.redis.set('STREAMING', 1)  # set STREAMING

    @catch_database_errors
    def stop(self):
        """ Removes "STREAMING" key in database """
        self.bookmarks.clear()  # clear all bookmarks when live stream is stopped.
        self.redis.delete('STREAMING')  # unset STREAMING key

    @catch_database_errors
    def _save_to_disk(self):
        """
        Saves current database in memory to disk.
        This is in a separate method so I could add the @catch_database_errors decorator
            instead of rewriting those try/except clauses.
        """
        self.redis.save()

    @catch_database_errors
    def save(self, filename=None, shutdown=False):
        """
        Save the current database to disk
        <save> whether to save the file in the storage directory,
            and returns the full filename used (may not be the same as given)
        """
        # Todo: Make this more robust to possible errors.
        #  Check redis's last update time before and after to check if it changed, indicating a successful save

        n = 1
        try:
            self._save_to_disk()
        except DatabaseTimeoutError:  # busy saving - unresponsive
            print("Saving database to disk... ({})".format(n))
            n += 1
            sleep(2)

        while True:  # wait while database gives Timeout Errors.
            try:
                self.ping()  # check to see if database is responsive yet
                break
            except DatabaseTimeoutError:
                print("Saving database to disk... ({})".format(n))
                n += 1
                sleep(5)
            except Exception as e:
                raise DatabaseError("Failed to save database to disk. {}: {}".format(e.__class__.__name__, e))

        if not path.isfile(self.live_path+'/'+self._file):
            raise DatabaseError("Failed to save database file - no database file was found")

        # if file name not given or already exists
        if not filename or path.isfile(self.save_path+'/'+filename):
            filename = get_time_filename()
        if not filename.endswith('.rdb'):
            filename += '.rdb'

        stat = system("cp {}/{} {}/{}".format(self.live_path, self._file, self.save_path, filename))
        if stat < 0:
            raise DatabaseError("Failed to save database file to '{}': Status code: {}".format(filename, stat))

        # check to make sure that the new file was indeed created
        if not path.isfile("{}/{}".format(self.save_path, filename)):
            raise DatabaseError("Failed to save database file to '{}'. Aborting database wipe.".format(filename))

        try:  # clear contents of live dump file
            self.redis.flushdb()
        except Exception as e:
            raise DatabaseError("Failed to flush database file '{}': {}".format(filename, e))

        if shutdown:
            try:
                self.redis.shutdown()
            except Exception as e:
                raise DatabaseError("Failed to shut down database: {}".format(e))

        return filename

    @catch_database_errors
    def time_since_save(self):
        """ Returns time since last save in integer seconds """
        last_save = self.redis.execute_command("LASTSAVE")  # returns a datetime object
        return int(time() - last_save.timestamp())

    @catch_database_errors
    def get_start_time(self):
        """ Get the time that streaming started form the database """
        response = self.redis.get("START_TIME")  # get START_TIME key
        if response:
            return float(response)
        # return None if no response

    @catch_database_errors
    def shutdown(self):
        """ Shutdown the redis server instance """
        self.save(shutdown=True)


class PlaybackDatabase(ServerDatabase):
    """
    Database class used for a playback database connection on the server.
    Replaces many methods used by Database, and cannot perform writes to the database.
    Reads database contents relative to self-contained start/stop times.
    Created only by DatabaseController.new_playback()
    """
    def __init__(self, ip, port, password, file):
        super().__init__(ip, port, password, file)
        self.playback_speed = 5                # speed multiplier
        self.playback_active = False            # whether this connection is actively playing back
        self.start_time = time()*1000           # real time playback was last started (ms)
        self.relative_stop_time = time()*1000   # time (relative to playback) that playback was last paused (ms)

    def __repr__(self):
        return "Playback, {}:{}, {}".format(self.ip, self.port, self.file)

    @property
    def live(self):
        return False

    def is_streaming(self):
        """ Check self.playback_active property """
        return self.playback_active

    def time(self):
        """ Get current playback time (affected by starting and stopping the playback) """
        if self.is_streaming():  # playback is active
            # time since started (ms)
            diff = (time()*1000-self.start_time) * self.playback_speed
            return self.relative_stop_time + diff  # time difference after last stopped
        else:  # playback is paused
            return self.relative_stop_time  # only return the time at which it was paused

    @catch_database_errors
    def start(self):
        """
        Live mode: Sets "STREAMING" key in database.
        Playback mode: Starts playback.
        """
        if self.is_streaming():  # already streaming
            return
        self.start_time = time() * 1000  # mark last playback start time (ms)
        self.playback_active = True

    @catch_database_errors
    def stop(self):
        """ Pauses playback """
        self.relative_stop_time = self.time()  # mark playback pause time relative to playback
        self.playback_active = False

    @catch_database_errors
    def _shutdown(self):
        """
        Sends a shutdown signal to redis.
        This is in a separate method so I could add the @catch_database_errors decorator
            instead of rewriting those try/except clauses.
        """
        self.redis.shutdown(save=False)

    @catch_database_errors
    def shutdown(self):
        """ Shutdown the redis server instance """
        try:  # shutdown playback database without saving
            self._shutdown()
        except DatabaseTimeoutError:
            sleep(2)  # wait a sec
        except Exception as e:
            raise DatabaseError("Failed to shutdown database on port: {}. {}: {}".format(self.port, e.__class__.__name__, e))

        while True:
            try:
                self.ping()  # should fail with a connection error
                raise DatabaseError("Failed to shutdown database on port {}: Successful ping after shutdown".format(self.port))
            except DatabaseConnectionError:
                return  # successfully shut down
            except DatabaseTimeoutError:
                sleep(2)  # wait a bit then check again
            except Exception as e:
                raise DatabaseError("Unknown exception occurred when checking to make sure the database was shutdown: {}: {}".format(e.__class__.__name__, e))

    @catch_database_errors
    def get_total_time(self, stream):
        """ Gets the total length in time of a given stream in seconds """
        bookmark = self.bookmarks[stream]
        if not bookmark:
            return 0

        start_id = bookmark.first_id
        if not start_id:
            try:
                bookmark.first_id = self.decode(self.bytes_redis.xrange('stream:'+stream, count=1)[0][0])
                start_id = bookmark.first_id
            except Exception as e:
                return 0

        end_id = bookmark.end_id
        if not end_id:
            try:
                # Must use bytes redis here because if the data column has un-decodable bytes
                # data (like for the video), then it will throw an error trying to read it.
                bookmark.end_id = self.decode(self.bytes_redis.xrevrange('stream:'+stream, count=1)[0][0])
                end_id = bookmark.end_id
            except Exception as e:
                return 0

        start_time = self.redis_to_time(start_id)
        end_time = self.redis_to_time(end_id)
        diff = end_time - start_time
        return diff/1000  # ms to s

    def write_data(self, *args, **kwargs):
        """ Not allowed """
        raise DatabaseError("Cannot write to read-only playback database")

    def write_snapshot(self, *args, **kwargs):
        """ Not allowed """
        raise DatabaseError("Cannot write to read-only playback database")

    @catch_database_errors
    def read_data(self, stream, count=None, max_time=None, numerical=True, to_json=False, decode=True, downsample=False):
        """
        Gets newest data for <reader> from data column <stream>.
        <stream> is some ID that identifies the stream in the database.
        <reader> is some ID that will keep track of it's own read head position.
        <count> Not implemented in playback mode.
        <max_time> maximum time window (s) to read.
            - If None, read as much as possible (guarantees all data read)
        <numerical>  Whether the data needs to be converted python float type
        <decode> Whether to decode the result into strings.
            If False, only values will remain as bytes. Keys will still be decoded.
        <to_json> whether to convert to json string. if False, uses dictionary of lists.
        """
        if not stream:
            return

        # get bookmark for this stream, create if not exist
        bookmark = self.bookmarks.get(stream)
        if bookmark.lock(block=False):  # acquire lock
            print("Blocked from reading")
            return  # return if already locked

        print('acquired lock on {}'.format(stream))

        t0 = time()
        if decode:
            red = self.redis
        else:
            numerical = False
            red = self.bytes_redis

        if bookmark.last_id and bookmark.last_time:  # last read spot exists
            last_read_id = bookmark.last_id
            last_read_time = bookmark.last_time
            temptime = self.time()
            time_since_last = self.time()-last_read_time  # time since last read (ms)

            if downsample and self.playback_speed > 1:
                max_time = max_time*self.playback_speed

            # if time since last read is greater than maximum, increment last read ID by the difference
            if max_time and time_since_last > max_time*1000:  # max_time is in seconds
                new_time = self.redis_to_time(last_read_id) + (time_since_last-max_time*1000)
                last_read_id = self.time_to_redis(new_time)  # convert back to redis timestamp

            # calculate new ID by how much time has passed between now and beginning
            first_read_id = bookmark.first_id
            first_read_time = bookmark.first_time
            time_since_first = self.time()-first_read_time
            new_time = self.redis_to_time(first_read_id) + time_since_first
            max_read_id = self.time_to_redis(new_time)

            t1 = time()

            if downsample and self.playback_speed > 1:  # get downsampled response if playing at high multiplier
                response = self._downsample(stream, last_read_id, max_read_id)
            else:
                # Redis uses the prefix "(" to represent an exclusive interval for XRANGE
                response = red.xrange('stream:'+stream, min='('+last_read_id, max=max_read_id)

        else:  # no last read spot
            t1 = time()
            response = red.xrange('stream:'+stream, count=1)  # read first data point

        if not response:
            print('released early {}'.format(stream))
            bookmark.release()
            return None

        t2 = time()

        # response is a list of tuples. First is the redis timestamp ID, second is the data dict.

        # set first-read info if not already set
        if not bookmark.first_time:
            bookmark.first_time = self.start_time  # get first time
            bookmark.first_id = self.decode(response[-1][0])  # get first ID

        # set last-read info
        bookmark.last_time = self.time()
        bookmark.last_id = self.decode(response[-1][0])  # store last timestamp

        t3 = time()
        # create final output dict
        output = {}

        # loop through stream data and convert if necessart

        # I hate this
        if numerical and decode:
            for data in response:
                d = data[1]  # data dict. data[0] is the timestamp ID
                for key in d.keys():
                    if output.get(key):
                        output[key].append(float(d[key]))  # convert to float and append
                    else:
                        output[key] = [float(d[key])]

        elif numerical and not decode:
            for data in response:
                d = data[1]  # data dict. data[0] is the timestamp ID
                for key in d.keys():
                    k = key.decode('utf-8')  # key won't be decoded, but it needs to be
                    if output.get(k):
                        output[k].append(float(d[key]))  # convert to float and append
                    else:
                        output[k] = [float(d[key])]

        elif not numerical and decode:
            for data in response:
                # data[0] is the timestamp ID
                d = data[1]  # data dict
                for key in d.keys():
                    if output.get(key):
                        output[key].append(d[key])  # append
                    else:
                        output[key] = [d[key]]

        elif not numerical and not decode:
            for data in response:
                # data[0] is the timestamp ID
                d = data[1]  # data dict
                for key in d.keys():
                    k = self.decode(key)  # key won't be decoded, but it needs to be
                    if output.get(k):
                        output[k].append(d[key])  # append
                    else:
                        output[k] = [d[key]]

        t4 = time()

        if to_json:
            result = json.dumps(output)
            t5 = time()
            print("INFO: {:5f}, READ: {:5f}, META: {:5f}, CONV: {:5f}, JSON: {:5f}".format(t1-t0, t2-t1, t3-t2, t4-t3, t5-t4))
        else:
            result = output

        print('released {}'.format(stream))
        bookmark.release()  # release lock
        return result

    @catch_database_errors
    def read_snapshot(self, stream, to_json=False, decode=True):
        """
        Gets latest snapshot for <reader> from data column <stream>. Only gets 1 data point.
        <stream> is some ID that identifies the stream in the database.
        <to_json> whether to convert to json string. if False, uses dictionary of lists.
            - Also note that this removes the 'time' data column. This is for
                plotting purposes - plotting software requires that all columns
                be of same length, and the time column only has one entry.
        Since this is a snapshot (not time series), gets last 1 data point from redis.
        """
        if not stream:
            return

        # get bookmark for this stream. Create a new one if it doesn't exist.
        bookmark = self.bookmarks.get(stream)
        if bookmark.lock(block=False):  # acquire lock
            return  # return if already locked

        if decode:
            red = self.redis
        else:
            red = self.bytes_redis  # not implemented for this method

        if bookmark.last_id and bookmark.last_time:  # last read spot exists
            last_read_id = bookmark.last_id
            last_read_time = bookmark.last_time
            first_read_id = bookmark.first_id
            first_read_time = bookmark.first_time
            temptime = self.time()
            time_since_first = self.time() - first_read_time
            max_time = self.redis_to_time(first_read_id) + time_since_first
            max_read_id = self.time_to_redis(max_time)
            response = red.xrevrange('stream:'+stream, min='('+last_read_id, max=max_read_id, count=1)
            #print("\n[{}] now_time: {}, time_since: {}, \n    last_read_id: {},   max_id: {}".format(stream[:5], h(temptime), h(time_since_first), h(last_read_id), h(max_read_id)))

        else:  # no first read spot exists
            response = red.xrange('stream:'+stream, count=1)  # get the first one

        if not response:
            bookmark.release()  # release lock
            return None

        # set first-read info if not already set
        # still necessary for calculating elapsed time
        if not bookmark.first_time:
            bookmark.first_time = self.start_time  # get first time
            bookmark.first_id = self.decode(response[0][0])  # get first ID

        # set last-read info
        bookmark.last_time = self.time()
        bookmark.last_id = self.decode(response[0][0])  # store last timestamp

        data = response[0][1]  # data dict (only one data point)
        keys = data.keys()  # get keys from data dict
        output = {key: [] for key in keys}

        for key in keys:
            vals = data[key].split(',')
            output[key] = [float(val) for val in vals]

        if to_json:
            del output['time']  # remove time column for json format
            result = json.dumps(output)
        else:
            result = output
        bookmark.release()  # release lock
        return result

    @catch_database_errors
    def _downsample(self, stream, last_id, max_id):
        """
        Returns the raw redis response from <last_id> to <max_id> (not including last_id),
            but downsampled according to the current playback speed.
        Not meant to be called directly. Used by other read_data() methods.
        Assumes that the bookmark for this stream already exists and it's lock has been acquired.
        Assumes that normal redis is being used (not bytes_redis).
        If no 'sample_rate' key is found for this stream, assumes 10Hz.
            - This will over-downsample streams over 10Hz,
                under-downsample streams between 10Hz/playback_speed and 10Hz,
                and streams under 10Hz/playbackspeed will be unaffected.
        """
        # TODO: implement downsampling even for live reading. This would require setting some
        #  kind of frequency cap that downsamples anything above like 10x that cap down to that cap.
        #  (because downsampling something only 2x the cap is inefficient since I would then be
        #  manually reading every other data point). This is simple, you just gotta know the sample rate
        #  to make that decision.
        bookmark = self.bookmarks[stream]
        if not bookmark.sample_rate:  # if no sample rate, find it
            bookmark.sample_rate = self.get_info(stream, 'sample_rate')
            if not bookmark.sample_rate:  # no sample rate given in info data column
                bookmark.sample_rate = 10*self.playback_speed  # downsample to 10Hz
            else:
                bookmark.sample_rate = int(bookmark.sample_rate)

        # time in ms of downsampled data chunk
        bucket_size = 1000*self.playback_speed / bookmark.sample_rate

        # integer milliseconds of the given redis IDs
        last_id_time = self.redis_to_time(last_id)
        max_id_time = self.redis_to_time(max_id)

        pipe = self.redis.pipeline()  # pipeline queues a series of commands at once
        while last_id_time < max_id_time:
            start_id = self.time_to_redis(last_id_time)  # start of bucket range
            last_id_time += bucket_size  # increment by bucket size
            end_id = self.time_to_redis(last_id_time)  # end of bucket range
            pipe.xrevrange('stream:'+stream, min='('+start_id, max=end_id, count=1)

        # list of lists of tuples
        raw_response = pipe.execute()
        response = []
        for lst in raw_response:
            if lst:  # pick out only results with data
                response.append(lst[0])  # put the single data point into the response list

        print(len(response))
        # response is now in same format as if read by a single XRANGE command
        return response


class Bookmarks:
    """
    Holds a bunch of Bookmark objects indexed by an ID.
    Basically a wrapper around a dictionary, but doesn't throw errors
        when indexing non-existent items and can create new ones.
    """
    def __init__(self):
        self.bookmarks = {}

    def __getitem__(self, ID):
        """ Returns the indexed bookmark or None if it doesn't exist """
        return self.bookmarks.get(ID)

    def get(self, ID):
        """ Returns the indexed bookmark. If none exists, create it. """
        bookmark = self.bookmarks.get(ID)
        if not bookmark:
            bookmark = Bookmark()  # create new Bookmark if not found
            self.bookmarks[ID] = bookmark
        return bookmark



    def clear(self):
        """ Clear all bookmarks """
        self.bookmarks = {}


class Bookmark:
    """
    Used by Database classes to keep track of read positions.
    To ensure serialized access to members, must use provided lock() and release() methods.
    """
    def __init__(self):
        self._lock = Lock()
        self.first_id = None  # first read database timestamp ID
        self.last_id = None   # last read database timestamp ID
        self.first_time = None  # real time when first read
        self.last_time = None   # real time when last read
        self.end_id = None    # last database timestamp ID in the whole stream

        self.sample_rate = None  # sample rate of a given stream, if applicable

        self.write = None  # last written database time in INTEGER MILLISECONDS
        self.seq = None    # last written database SEQUENCE NUMBER

    def lock(self, block):
        """
        Attempt to acquire the lock for this bookmark.
        Must be called before accessing any member variables.
        If lock is acquired, returns True.
        If already locked, returns False.
        """
        return self._lock.acquire(block=block)

    def release(self):
        """ Releases the lock for this bookmark """
        self._lock.release()

    def clear(self):
        """ Clear all values """
        self.first_id = None
        self.last_id = None
        self.first_time = None
        self.last_time = None
        self.end_time = None
        self.ms = None
        self.seq = None











