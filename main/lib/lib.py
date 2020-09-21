from threading import Thread, Lock, Condition, Event, current_thread
from multiprocessing import Process, current_process, Pipe
from uuid import uuid4
import traceback
import socket
from datetime import datetime
import time
import json


PAGES_DIR = './pages'


class Base:
    """
    Base class from which all others inherit.
    Implements global logging functionality.
    """
    debug_level = 0  # debugging level
    last_msg = ''    # to keep track of the last output message
    print_lock = Lock()  # lock on output

    def __init__(self):
        self.exit = False  # status flag  determine when the class should stop running
        self.close = False  # status flag to determine when the class should completely shutdown
        self.exit_condition = Condition()  # condition lock to stop running
        self.shutdown_condition = Condition()  # condition lock block until everything is completely shutdown
        self.encoding = 'iso-8859-1'  # encoding for all data streams (latin-1)

    def run_exit_trigger(self, block=False):
        """
        Runs exit trigger.
        If non-blocking, the exit trigger runs on a new thread (KeyboardInterrupts will be ignored)
        If blocking, the exit trigger runs on the current thread. If run on the MainThread,
            KeyboardInterrupts will be caught. Otherwise, they will be ignored.
        """
        if not block:  # run exit trigger on separate thread
            Thread(target=self._exit_trigger, name='CLEANUP', daemon=True).start()
        else:  # run exit trigger on current thread
            self._exit_trigger()

    def _exit_trigger(self):
        """
        Waits until exit status is set.
        When triggered, calls cleanup if close status is set
        Should be run on it's own thread.
        """
        try:
            with self.exit_condition:
                self.exit_condition.wait()  # wait until notified
        except KeyboardInterrupt:
            if current_process().name == 'MainProcess':
                self.log("Manual Termination")  # only display log on main process
            self.shutdown()  # set both exit and close flags
        except Exception as e:
            self.debug("Unexpected exception in exit trigger of '{}': {}".format(self.__class__.__name__, e))
        finally:
            if self.close:  # if close flag is set
                self.cleanup()
            with self.shutdown_condition:
                self.shutdown_condition.notify_all()  # notify that cleanup has finished

    def set_debug(self, level):
        Base.debug_level = level
        if self.debug_level > 0:
            print()  # display message
            print("------------------------------")
            print("RUNNING IN DEBUG MODE {}".format(Base.debug_level))
            print('[TIME][PROCESS][THREAD]: Message')
            print("------------------------------")

    def get_date(self):
        """ Return the current date and time in HTTP Date-header format """
        return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

    def get_time(self):
        """ Return the current time for debugging purposes """
        return datetime.now().strftime("%-H:%-M:%-S:%f")

    def display(self, msg):
        """ display a log message """
        if Base.last_msg == msg:  # same message
            return  # Ignore duplicate messages
        Base.print_lock.acquire()
        Base.last_msg = msg
        print(msg)
        Base.print_lock.release()

    def log(self, message):
        """ Outputs a log message """
        self.display("> {}".format(message))

    def debug(self, msg, level=1):
        """ Sends a debug level message """
        if Base.debug_level >= level:
            self.display("[{}][{}][{}]: {}".format(self.get_time(), current_process().name, current_thread().name, msg))

    def throw(self, msg, cause=None, trace=True):
        """ display error message and halt """
        self.shutdown()  # set both exit and shustdown flags
        err = "\nERROR: {}\n".format(msg)
        if cause is not None:
            err += "CAUSE: {}\n".format(cause)
        self.display(err)
        if trace:
            Base.print_lock.acquire()
            traceback.print_exc()
            Base.print_lock.release()

    def generate_uuid(self):
        """ Return a UUID as a URN string """
        return uuid4().urn[9:]

    def halt(self):
        """ Set only the exit flag and exit """
        self.exit = True  # signal to stop running
        with self.exit_condition:
            self.exit_condition.notify_all()  # notify waiting thread

    def shutdown(self, block=False):
        """
        set both the exit flag and shutdown flag.
        If block is True, waits for shutdown to complete.
        """
        if self.close and self.exit:  # shutdown already called
            return
        self.halt()
        self.close = True
        if block:
            with self.shutdown_condition:
                self.shutdown_condition.wait()  # wait until cleanup is finished

    def cleanup(self):
        """
        Should not be called directly.
        Should be overwritten to do anything that should happen before terminating.
        This method is called in _exit_trigger.
        """
        pass


class SocketHandler(Base):
    """
    Handles incoming requests on a single socket
    <sock> is the streaming socket connected to either the data server or streaming client
    <node> is the object which defines the request methods.
    <name> Optional Name of the socket. If none is give, 'SOCKET' will be used.
    <uuid> Optional ID. If none is give, one will be generated.
    """
    def __init__(self, sock, node, name=None, uuid=None):
        super().__init__()

        self.socket = sock
        self.node = node
        if not name:
            name = 'SOCKET'
        self.name = name
        if not uuid:
            self.id = self.generate_uuid()  # unique id
        else:
            self.id = uuid

        self.ip = "{}:{}".format(*sock.getsockname())    # local address of this socket
        self.peer = "{}:{}".format(*sock.getpeername())  # address of machine on other end of connection                          # unique identifier. Right now it's just the ip of the connecting socket - no need for anything more complicated yet.

        self.pull_buffer = sock.makefile('rb')  # incoming stream buffer to read from
        self.pull_lock = Condition()            # lock for pull_buffer

        self.push_buffer = sock.makefile('wb')  # outgoing stream buffer to write to
        self.push_lock = Condition()            # lock for push_buffer

        self.request = Request(origin=self)     # current request being parsed

    def run(self):
        """
        Main entry point.
        Starts running on a new thread.
        """
        if not self.node:  # no parent connection set
            self.throw("No parent Node has been assigned for SocketHandler '{}'".format(self.name))
            return
        if not hasattr(self.node, 'HANDLE'):  # no HANDLE method
            self.throw("Parent Node '{}' of SocketHandler '{}' has no HANDLE method".format(self.node.name, self.name))
            return
        Thread(target=self._run, name=self.name+'-RUN', daemon=True).start()  # start handler thread
        self.run_exit_trigger()  # start thread waiting for exit status

    def _run(self):
        """
        Continually parse and handle requests until exit status set.
        Blocks until something is read from the socket.
        Should be called on it's own thread.
        """
        try:
            while not self.exit:  # loop until exit condition
                with self.pull_lock:
                    request = self.parse_request()  # get the next full request
                    if request:
                        self.node.HANDLE(request, threaded=True)   # allow the request to be handled by the parent Connection
        except Exception as e:
            self.throw("Unexpected Exception in running socket: {}".format(self.name), e, trace=True)
            self.shutdown()

    def parse_request(self):
        """
        Reads a single request from the stream.
        Returns a request object when fully parsed.
        Returns None if not yet finished.
        """
        if not self.parse_request_line():
            return  # error
        if not self.parse_header():
            return  # error
        if not self.validate():  # validate request headers before parsing content
            return  # error
        if not self.parse_content():
            return  # error

        parsed_request = self.request
        self.request = Request(origin=self)  # ready for new request
        return parsed_request   # return parsed request

    def validate(self):
        """
        Looks for keywords in the Host and User-Agent headers.
        Right now just used to block some random web scraping bots that keep finding my server
        """
        host_keywords = ['webredirect', 'ddnsfree']
        agent_keywords = ['nimbostratus', 'cloudsystemnetworks', 'bot']  # keywords to look for in User-Agent header
        host_string = self.request.header.get('host'),
        agent_string = self.request.header.get('user-agent')
        valid = True
        if host_string:
            for keyword in host_keywords:
                if keyword in host_string:
                    valid = False
        if agent_string:
            for keyword in agent_keywords:
                if keyword in agent_string:
                    valid = False
        if not valid:
            self.throw("Blocked Connection from banned source:\n   Address: {}\n   Host: {}\n   User-Agent: {}".format(self.peer, host_string, agent_string))
            self.debug("FULL HEADER: {}".format(self.request.header))
        return valid

    def read(self, length, line=False, decode=True):
        """
        Read and return data from the incoming buffer. Return None if not available.
        If <line> is False:
            - Reads exactly <length> amount from stream.
            - Returns everything including any whitespace
        If <line> is True:
            - Read from stream buffer until CLRF encountered, treating <length> as maximum
            - Returns single line without the trailing CLRF
            - Returns '' if the line received was itself only a CLRF (blank line)
            - if no CLRF before <length> reached, stop reading and throw throw
        Decode specifies whether to decode the data from bytes to string. If true, also strips whitespace.
        Returns None on error.
        """
        try:
            if line:  # read a single line
                CRLF = b'\r\n'
                data = self.pull_buffer.readline()  # read from stream
                while data and data[-2:] != CRLF and not self.exit:  # doesn't end in newline
                    data += self.pull_buffer.readline()  # keep adding
                    data_len = len(data)
                    if data_len > length:  # data too big - newline not found before maximum
                        prev = 20 if data_len > 40 else int(data_len/2)
                        self.throw("Couldn't read a line from the stream - maximum length reached (max:{}, length:{})".format(length, data_len), "{} ... {}".format(data[prev:], data[data_len-prev:]))
                        raise BrokenPipeError
            else:  # exact length specified
                data = self.pull_buffer.read(length)
                data_len = len(data)
                while data and data_len < length and not self.exit:  # didn't get full length
                    length -= data_len
                    data += self.pull_buffer.read(length)  # pull remaining length
                    data_len = len(data)

            if not data:  # no data read - disconnected
                raise BrokenPipeError
            if decode:
                data = data.decode(self.encoding).strip()  # decode and strip whitespace including CLRF
            return data
        except (ConnectionResetError, BrokenPipeError) as e:  # disconnected
            self.debug("Couldn't read from stream - Socket closed. Disconnecting.", 3)
            self.shutdown()

    def parse_request_line(self):
        """
        Parses the Request-line of a request, finding the request method, path, and version strings.
        Return True if found, False if not
        """
        max_len = 256  # max length of request-line before throw (arbitrary choice)

        line = ''
        while line == '':  # if blank lines are read, keep reading.
            line = self.read(max_len, line=True)  # read first line from stream
            if line is None:  # error reading
                return
        self.debug("Received Request-Line: '{}'".format(line.strip()), 3)
        words = line.split()
        if len(words) != 3:
            err = "Request-Line must conform to HTTP Standard ( METHOD /path HTTP/X.X   or   HTTP/X.X STATUS MESSAGE )"
            self.throw(err, line)
            return False
        # TODO: maybe add compatibility with shorter response lines without a version
        if words[0].startswith("HTTP/"):  # response
            self.request.version = words[0]
            self.request.code = words[1]
            self.request.message = words[2]
        else:                             # request
            self.request.method = words[0]
            self.request.version = words[2]
            words[1].strip('?')  # remove any trailing ?
            query_loc = words[1].find('?')
            if query_loc != -1:  # found a query
                path, query = words[1].split('?')
                self.request.path = path
                self.request.queries = dict([pair.split('=') for pair in query.split('&')])  # get param dict
            else:
                self.request.path = words[1]
        return True

    def parse_header(self):
        """
        Fills the header dictionary from the received header text.
        Return True if all are found, False if not
        """
        max_num = 32  # max number of headers (arbitrary choice)
        max_len = 1024  # max length each header (arbitrary choice)
        for _ in range(max_num):
            line = self.read(max_len, line=True)  # read next line in stream
            self.debug("Read Header '{}'".format(line), 4)
            if line is None:  # error reading
                return False
            if line == '':  # empty line signaling end of headers
                self.debug("All headers received", 3)
                self.request.header_received = True  # mark header as received
                break
            try:
                key, val = line.split(':', 1)  # extract field and value by splitting at first colon
            except ValueError:
                self.throw("Header line did not match standard format.", "Line: {}".format(line))
                return False

            self.request.header[key.lower()] = val.strip()  # remove extra whitespace from value
        else:
            self.throw("Too many headers", "> {}".format(max_num))
            return False
        return True

    def parse_content(self):
        """
        Parse request payload, if any.
        Return True if all are found, False if not
        """
        length = self.request.header.get("content-length")
        if length:  # if content length was sent
            content = self.read(int(length), decode=False)  # read raw bytes of exact length from stream
            if content is None:   # error reading
                return False
            self.debug("Received Content of length: {}".format(len(content)), 3)
            self.request.content = content
            return True
        else:  # no content length specified - assuming no content sent
            return True
            # TODO: Check request type to better determine whether a request could have content.

    def send(self, response):
        """ Compiles the response object then adds it to the out_buffer """
        data = response.get_data()
        if data is None:  # error which has been caught, hopefully
            self.throw("Request returned no data")
            return
        self.send_raw(data)

    def send_raw(self, data):
        """ Sends raw bytes data as-is to the out_buffer """
        try:
            with self.push_lock:
                self.push_buffer.write(data)
                self.push_buffer.flush()
                self.debug("Pushed data to stream", 3)
        except (ConnectionResetError, BrokenPipeError) as e:
            self.debug("Couldn't write to stream - socket closed. Disconnecting.", 3)
            self.shutdown()

    def send_multipart(self, buffer, request=None):
        """
        Continually creates and sends multipart-type responses to the stream.
        Used to send an image stream from an image_buffer
        NOTE:
            - If sending a stream to a browser, do not specify a request object.
            - If sending a stream to another connection object, use a request
                object to send a request method denoting which function to call
                for every data chunk sent - i.e. use add_request().
                Extra headers may also be added with add_header().
        """
        chunk_header = b'--DATA\r\n'  # sent along with every data chunk

        if not request:  # if no request is specified, assume a response header used to initially respond to browser requests.
            request = Request()
            request.add_response(200)
            request.add_header('Age', 0)
            #request.add_header('Cache-Control', 'no-cache')
            # res.add_header('Connection', 'keep-alive')

            chunk_header += b'content-type:image/jpeg\r\n'  # when sending to browser, always use jpeg image type in the chunk headers

        request.add_header('content-type', 'multipart/x-mixed-replace; boundary=DATA')
        request.add_content('')
        self.send(request)

        try:
            self.debug("Started multipart stream", 1)
            lock = buffer.get_read_lock()
            while not self.exit:
                data = buffer.read(lock)
                length_header = "Content-Length:{}\r\n".format(len(data)).encode(self.encoding)  # content length + blank line
                packet = chunk_header + length_header + b'\r\n' + data + b'\r\n'
                self.send_raw(packet)
            self.debug("Ended multipart stream", 2)
        except Exception as e:
            self.throw('Multipart Stream Disconnected ({})'.format(self.peer), e)

    def parse_multipart(self, buffer):
        """ parses a multipart stream created by send_multipart. """
        # TODO: rewrite this. Maybe with an optional callback function for each chunk?
        self.debug("Receiving multipart stream", 2)
        try:
            boundary = False
            headers = False
            length = None
            while not self.exit:
                if not boundary:
                    boundary_header = self.read(32, line=True)
                    if boundary_header == '':
                        continue
                    self.debug("Boundary line: {}".format(boundary_header), 3)
                    boundary = True
                elif not headers:
                    for _ in range(5):
                        line = self.read(256, line=True)  # read next line in stream
                        self.debug("Read Chunk Header '{}'".format(line), 3)
                        if line == '':  # empty line signaling end of headers
                            self.debug("All chunk headers received", 3)
                            headers = True  # mark header as received
                            break
                        key, val = line.split(':', 1)  # extract field and value by splitting at first colon
                        if key.lower() == 'content-length':
                            length = int(val)
                    else:
                        self.throw("Too many headers", "> {}".format(5))
                        break
                else:
                    if not length:
                        self.throw("Did not receive content-length header")
                        return
                    data = self.read(length, decode=False)
                    buffer.write(data)
                    self.debug("Wrote to image buffer", 3)
                    boundary = False
                    headers = False
                    length = None
        except Exception as e:
            self.throw("BAD", e)

    def cleanup(self):
        """ Called when the handler stops running """
        try:
            self.socket.shutdown(socket.SHUT_RDWR)  # disallow further read and writes
            self.socket.close()  # close socket
        except (OSError, Exception) as e:
            self.debug("Error closing socket '{}': {}".format(self.name, e))
        finally:
            self.debug("Socket '{}' Closed on '{}' (ID: {})".format(self.name, self.node.name, self.id), 2)
            self.node.remove_socket(self.id)  # call the parent connection's method to remove this socket


class PipeHandler(Base):
    """
    An object representing a tunnel to a Node object running in another process
    <name> Name of the Connection
    <conn> Connection object returned by multiprocessing.Pipe()
    <process> Process that is running on the other end of the pipe
    """
    def __init__(self, name, conn, process=None):
        super().__init__()
        self.name = name
        self.pipe = conn
        self.process = process

    def send(self, payload):
        """ Send a message through the process pipe """
        try:
            self.pipe.send(payload)
        except:
            self.debug("Unable to send message through pipe {}".format(self.name), 2)

    def receive(self):
        """ Wait for then return a message from the pipe queue """
        try:
            return self.pipe.recv()
        except KeyboardInterrupt:
            self.debug("Pipe interrupted: '{}'".format(self.name))
        except EOFError:  # connection closed
            self.debug("EOF: Pipe '{}' closed".format(self.name))
        except Exception as e:
            self.debug("Failed to read from pipe '{}': {}".format(self.name, e), 2)

    def terminate(self):
        """ Terminate the process after a shutdown window """
        if not self.process:  # is a worker pipe
            return
        if not self.process.is_alive():  # if already terminated
            return
        self.process.join(0.1)  # allow process time to shutdown
        time.sleep(0.1)  # give the process time to register as terminated
        if self.process.is_alive():  # if the process is still alive
            self.process.terminate()  # forcibly terminate it
            self.debug("Manually Terminated Process '{}'".format(self.name), 2)

    def close(self):
        """ Closes the connection """
        self.pipe.close()


class SocketPackage:
    """
    Holds a raw socket object and it's original ID.
    Optionally holds all attributes of a Request.
    Can be pickled and sent between processes.
    Use unpack() to reconstruct a SocketHandler object and Request object.

    <sock> A SocketHandler object, or a raw socket object.
    <request> The optional Request object
    """
    def __init__(self, sock, request=None):
        if type(sock) == SocketHandler:  # if a handler is given
            self.socket = sock.socket    # get raw socket
            self.id = sock.id            # get ID of handler
        else:
            self.socket = sock
            self.id = None

        if request:  # get the information to reconstruct a Request object
            self.request = True
            self.method = request.method
            self.path = request.path
            self.code = request.code
            self.message = request.message
            self.version = request.version
            self.queries = request.queries
            self.header = request.header
            self.content = request.content

    def unpack(self, node):
        """
        Returns a new SocketHandler and a Request object from this package
        Node is the new parent node for the SocketHandler.
        Request might be None if no request was sent in this package.
        """
        # create a SocketHandler from the raw socket, and provide the ID if it was given
        handler = SocketHandler(self.socket, node, uuid=self.id)

        req = None
        if self.request:  # if a request was given create a new Request object
            req = Request()
            req.origin = handler
            req.method = self.method
            req.path = self.path
            req.code = self.code
            req.message = self.message
            req.version = self.version
            req.queries = self.queries
            req.header = self.header
            req.content = self.content

        return handler, req


class Node(Base):
    """
    Base class for Connection Nodes.
    Holds an index of sockets assigned to it, and provides request methods to handle requests.
    Holds an index of IPC pipes to other Nodes.
    """
    def __init__(self, name=None, device='Unnamed_Device'):
        super().__init__()
        if not name:
            name = self.__class__.__name__
        self.name = name
        self.device = device
        self.id = self.generate_uuid()  # unique id

        self.sockets = {}  # index of SocketHandlers associated with this connection. Keys are the ID of the socket

    def add_socket(self, socket_handler):
        """ Add a new SocketHandler the index and return the ID"""
        self.sockets[socket_handler.id] = socket_handler
        self.debug("Added socket '{}' to '{}'".format(socket_handler.name, self.name), 2)
        return socket_handler.id

    def remove_socket(self, socket_id):
        """
        Remove a SocketHandler from the socket index.
        The SocketHandler must be deactivated to remove it.
        This means that either SocketHandler.halt() or SocketHandler.shutdown() is called beforehand.
        If SocketHandler.halt() is called, it is assumed that the live raw_socket is to be re-used with another handler.
        """
        handler = self.sockets.get(socket_id)
        if not handler:  # not found - socket has already been removed or never existed
            self.debug("Redundantly removed Socket (ID: {}) from {}".format(socket_id, self.name), 2)
            return
        if not handler.exit:  # exit status on socket not set
            self.throw("Attempted to remove a live SocketHandler '{}' (ID: {}) from node '{}'".format(handler.name, socket_id, self.name), trace=False)
            return
        self.debug("Removing socket '{} (ID: {})' from node '{}'".format(handler.name, socket_id, self.name), 2)
        del self.sockets[socket_id]  # remove it from the socket index

    def transfer_socket(self, pipe, socket_handler, request=None):
        """
        Send a live raw socket through a process pipe.
        Packages the socket and optional request in order to send it through the pipe.
        """
        request.origin.halt()  # stop handler first
        package = SocketPackage(socket_handler, request)  # package the raw socket and request
        pipe.send(package)  # send package through pipe
        self.remove_socket(request.origin.id)  # remove handler from host index
        self.debug("'{}' sent a socket to '{}'".format(self.name, pipe.name), 2)

    def receive_socket(self, package):
        """
        Unpacks a SocketPackage to create a SocketHandler and a possible Request
        Runs the Request on that socket, then runs the SocketHandler to receive subsequent requests.
        """
        self.debug("Node '{}' received a transferred socket.".format(self.name), 2)

        # get the new SocketHandler (with self as the new parent node) and optional Request
        handler, request = package.unpack(self)
        if request:  # if a request was sent with the socket
            if request.method == 'SIGN_ON':
                handler.name = 'DATA-SOURCE'
            else:
                handler.name = 'REQUESTER'
            self.HANDLE(request)  # handle the request on a new thread

        self.add_socket(handler)  # add this handler to the socket index
        handler.run()  # then start handling other requests from the socket (on a new thread)

    def send(self, request, socket_handler):
        """ Sends a Request object through a socket. """
        socket_handler.send(request)

    def HANDLE(self, request, threaded=True):
        """
        Method called for every request that is sent to any of the Connection's sockets.
        Calls other request methods according to the request type (e.g. GET, POST, INIT, etc...)
        """
        if request.method:  # received request method
            if threaded:  # run command on a new thread
                Thread(target=self._execute, args=(request,), name=self.name+'-'+request.method, daemon=True).start()
            else:
                self._execute(request)
        elif request.code:  # received a response code
            pass
            # TODO: handle response codes from clients?
        else:
            self.throw("Unknown request/response type?", "Nether a request method nor a response code were found in the data sent.")

        # content_type = request.header.get('content-type')
        # if content_type and content_type.split(';')[0] == 'multipart/x-mixed-replace':  # multipart stream

    def _execute(self, request):
        """ Execute the specified command """
        if not hasattr(self, request.method):  # if a method for the request doesn't exist
            self.throw('Unsupported Request Method {} for {}'.format(request.method, self.name))
            return
        else:
            method_func = getattr(self, request.method)  # get handler method that matches name of request method

        try:
            method_func(request)  # execute command and pass along the request that called it
        except Exception as e:
            self.throw("Error in command: {}".format(request.method), e, trace=True)

    def cleanup(self):
        """ Shutdown all sockets """
        for sock in list(self.sockets.values()):  # force as iterator because items are removed from the dictionary
            sock.shutdown(block=True)  # block until socket shuts down
        self.debug("All Sockets shut down on Node '{}'".format(self.name))


class HostNode(Node):
    """
    The main hosting connection (a Server or Client).
    Run on the main process.
    Optionally delegates incoming sockets to other worker Connections.
    Worker connections are started on new processed and communicated with using pipes.
    <auto> Boolean: whether to automatically shutdown when all worker nodes have shutdown.
    """
    def __init__(self, name, auto=True):
        super().__init__(name=name, device=name)  # name and device name are the same
        self.automatic_shutdown = auto
        self.pipes = {}   # index of Pipe objects, each connecting to a WorkerConnection

    def run_worker(self, worker):
        """
        Start the given worker node on a new process.
        Adds the new Pipe to the pipe index, and starts reading from it on a new thread.
        Return the new Pipe.
        """
        host_conn, worker_conn = Pipe()  # multiprocessing duplex connections (doesn't matter which)

        # worker knows host name but not the host process
        worker_pipe = PipeHandler(self.name, host_conn)

        # process for new worker
        worker_process = Process(target=worker.run, args=(worker_pipe,), name=worker.name, daemon=True)

        # host knows worker name and process. Pipe is indexed by the worker ID
        self.pipes[worker.id] = PipeHandler(worker.name, worker_conn, worker_process)

        # new thread to act as main thread for process
        Thread(target=worker_process.start, name='WorkerMainThread', daemon=True).start()

        # read from this pipe on a new thread
        Thread(target=self._run_pipe, args=(worker.id,), name=self.name+'-PIPE', daemon=True).start()

    def remove_worker(self, pipe_id):
        """
        Removes a worker pipe from the index
        Note that this does not terminate the worker process
        """
        pipe = self.pipes.get(pipe_id)
        if not pipe:  # ID not found in index
            self.debug("Host Failed to remove worker Node '{}'. Not found in worker dictionary. \
                This could be caused by a worker sending two SHUTDOWN signals.".format(pipe.name))
            return
        del self.pipes[pipe_id]  # remove from index
        if self.automatic_shutdown:
            if not self.pipes:  # all worker have disconnected
                self.debug("No workers left - shutting down '{}'".format(self.name))
                self.shutdown()

    def _run_pipe(self, pipe_id):
        """
        Continually waits for messages coming from the pipe.
        Handles the message by calling other methods.
        Should be run on it's own thread.
        """
        pipe = self.pipes[pipe_id]
        while not self.exit:
            message = pipe.receive()  # blocks until a message is received

            # Handle the message
            if type(message) == SocketPackage:  # A PickledRequest object containing a request and a new socket
                self.receive_socket(message)
            elif message == 'SHUTDOWN':  # the connection on the other end shut down
                self.debug("Host '{}' received SHUTDOWN from worker '{}'".format(self.name, pipe.name), 2)
                self.remove_worker(pipe_id)  # remove it
                return
            elif message is None:
                self.debug("Pipe to '{}' shut down unexpectedly on node '{}'".format(pipe.name, self.name), 2)
                return
            else:
                self.debug("Host Node Received unknown signal '{}' from Worker Node '{}'".format(message, pipe.name))

    def cleanup(self):
        """ End all worker process and sockets """
        super().cleanup()  # shutdown all sockets
        for pipe in list(self.pipes.values()):  # force as iterator because items are removed from the dictionary
            pipe.send('SHUTDOWN')  # signal all workers to shutdown
            pipe.terminate()  # terminate the process if not already
        self.debug("All worker nodes terminated on Host '{}'".format(self.name), 1)


class WorkerNode(Node):
    """
    This class holds a socket connection to a data-streaming client,
        plus all sockets requesting data from that client.

    <source> is the socket that connects to the Raspberry Pi that initiated the connection.
    <server> is the server object controlling this handler, which will pass on all subsequent socket connections
    """
    def __init__(self):
        super().__init__()
        self.source_id = None  # ID of the main data-streaming socket
        self.pipe = None  # Pipe object to the HostNode

    def run(self, pipe):
        """
        Main entry point.
        Run this Worker on a new process.
        pipe must be a PipeHandler leading to the host node.
        Should be run on it's own Process's Main Thread
        """
        self.pipe = pipe
        self.device = self.pipe.name
        self.debug("Started new worker '{}' on '{}'".format(self.name, self.device), 1)
        Thread(target=self._run, name='RUN', daemon=True).start()
        Thread(target=self._run_pipe, name='PIPE', daemon=True).start()
        self.run_exit_trigger(block=True)  # Wait for exit status on new thread

    def _run(self):
        """ Starts running all sockets, if any. """
        # run each socket (on a new thread)
        for handler in list(self.sockets.values()):
            self.debug("Running socket '{}' on '{}'".format(handler.name, self.name), 2)
            handler.run()

    def _run_pipe(self):
        """
        Continually waits for messages coming from the pipe.
        Handles the message by calling other methods.
        Should be run on it's own thread.
        """
        # Listen for messages on the hose pipe
        while not self.exit:
            message = self.pipe.receive()  # blocks until a message is received
            # Handle the message
            if type(message) == SocketPackage:  # A PickledRequest object containing a request and a new socket
                self.receive_socket(message)
            elif message == 'SHUTDOWN':  # Host Node signalled to shut down
                self.debug("Worker '{}' received SHUTDOWN from Host '{}'".format(self.name, self.pipe.name), 2)
                self.shutdown()
                return
            elif message is None:
                self.debug("Pipe to '{}' shut down unexpectedly on node '{}'".format(self.pipe.name, self.name), 2)
                return
            else:
                self.throw("Worker Node received unknown signal '{}' from Host Node '{}'. This really shouldn't have happened.".format(message, self.pipe.name))

    def set_source(self, raw_socket):
        """
        Set the source socket.
        Assumes that the SocketHandler previously associated with this socket (if any)
            has been halted and removed from the old node.
        """
        handler = SocketHandler(raw_socket, self, name='DATA-SOCKET')
        self.source_id = self.add_socket(handler)

    def remove_socket(self, socket_id):
        """ Overwrites default method """
        if socket_id == self.source_id:  # removing source socket
            self.debug("Removing source socket '{}' from {}) ".format(self.sockets[socket_id].name, self.name), 2)
            self.shutdown()  # start shutdown
            return
        super().remove_socket(socket_id)  # run default socket shutdown method

    def cleanup(self):
        """ Shutdown all handlers associated with this connection and signal that it shut down """
        super().cleanup()  # shutdown all sockets
        self.log("{} Closed.".format(self.name))
        self.pipe.send('SHUTDOWN')  # Signal to the server that this connection is shutting down


class Request(Base):
    """
    Holds all data from one request.
    Used by Handler class to store incoming/outgoing requests.
    To use, call the appropriate add_ methods to add parts of the HTTP request,
        then pass the object to Handler.send.

    <origin> is the Handler class containing the socket that received the request.
        This is so that a response can be sent back through the same socket.
    """

    def __init__(self, method=None, origin=None):
        super().__init__()
        self.origin = origin  # SocketHandler object which received this request

        self.method = None   # request method (GET, POST, etc..)
        if method:
            self.add_request(method)

        self.code = None  # HTTP response code
        self.message = None  # Response message

        self.version = 'HTTP/1.1'  # request version (HTTP/X.X)
        self.path = None     # request path string without queries
        self.queries = {}    # dict of queries, if any

        self.header = {}     # dictionary of headers
        self.content = None  # request content in bytes

    def __repr__(self):
        """ String representation shows only request line and headers """
        return self.format_request_line() + self.format_headers()

    def add_request(self, method, path='/', version='HTTP/1.1'):
        """ Add request method, path, and version """
        self.method = method
        self.path = path
        self.version = version
        self.debug("Added requestline: {}".format(self.format_request_line().replace('\r\n', '\\r\\n')), 3)

    def add_response(self, code):
        """ Add a response code, message, version, and default headers. Used to respond to web browsers. """
        self.code = code
        self.version = self.version
        messages = {200: 'OK', 204: 'No Content', 301: 'Moved Permanently', 304: 'Not Modified', 308: 'Permanent Redirect', 404: 'Not Found', 405: 'Method Not Allowed'}
        self.message = messages[code]
        self.add_header('Server', 'StreamingServer Python/3.7.3')  # TODO: make this not hard coded (Not really necessary yet)
        self.add_header('Date', self.get_date())
        self.debug("Added responseline: {}".format(self.format_request_line().replace('\r\n', '\\r\\n')), 3)

    def add_header(self, keyword, value):
        """ Add a single header line """
        self.header[keyword.lower()] = value
        self.debug("Added header: {}: {}".format(keyword.lower(), value), 4)

    def add_content(self, content):
        """ Add main content payload """
        if type(content) == str:
            data = content.encode(self.encoding)  # encode if string
        elif type(content) == bytes:
            data = content
        else:  # type conversion not implemented
            self.throw("Cannot send content - type not accounted for.", type(content))
            return
        if content:
            self.add_header('content-length', len(data))  # add content length header
        self.content = data
        self.debug("Added content of length: {}".format(len(data)), 3)

    def verify(self):
        """ Verify that this object meets all requirements and can be safely sent to the stream """
        if self.method and self.code:  # trying to send both a response and a request
            self.throw("Cannot send both a response and a request", 'You added request method "{}" and response code "{}"'.format(self.method, self.code))
            return False
        if not self.method and not self.code and self.header:
            self.throw("If you send a header, you must include a request or response as well")
            return False
        return True

    def format_request_line(self):
        """ Formats request/response according to HTTP standard"""
        if self.method:
            line = "{} {} {}\r\n".format(self.method, self.path, self.version)
        elif self.code:
            line = "{} {} {}\r\n".format(self.version, self.code, self.message)
        else:
            self.throw("Could not format request-line. No method or response code defined.")
            return ''
        return line

    def format_headers(self):
        """ Format headers according to HTTP standard """
        text = ''
        for key, value in self.header.items():
            text += "{}: {}\r\n".format(key, value)
        if self.header:  # at least one header exists
            text += '\r\n'  # add a blank line to denote end of headers
        return text

    def get_data(self):
        """ Formats all data into an encoded HTTP request and returns it """
        if not self.verify():
            return
        data = b''  # data to be returned

        data += self.format_request_line().encode(self.encoding)
        data += self.format_headers().encode(self.encoding)

        if self.content is not None:
            data += self.content  # content should already be in bytes
        else:
            data += b'\r\n'  # if no content, signal end of transmission

        return data


class Response(Request):
    """
    Inherits request. Same thing, but can be initialized with a response code instead.
    """
    def __init__(self, code=None):
        super().__init__()
        if code:
            self.add_response(code)


class DataBuffer(object):
    """
    A thread-safe buffer in which to store incoming data
    The write() method can be used by a Picam
    """
    def __init__(self):
        self.data = b''
        self.read_events = []  # list of all event conditions handed out
        self.write_condition = Condition()

    def get_read_lock(self):
        """ Returns a condition object that must be passed into the read() method. """
        event = Event()
        self.read_events.append(event)
        event.set()
        return event

    def read(self, event, json=False, block=True):
        """
        Returns the last data entry
        <event> is an event object received from get_read_lock(). Acts as a 'ticket' to read the data
        <json> Unused. Arguments just need to match other buffer types.
        <block> is whether the read blocks before a new write is available.
        """
        if block:
            event.wait()  # triggers when data is available
            event.clear()  # reset event
            return self.data
        else:  # non-blocking
            if event.is_set():  # ready to be read
                event.clear()  # reset event
                return self.data
            else:  # data not ready
                return None

    def write(self, new_data):
        """
        Waits on a single write condition.
        Only one write is allowed at a time
        """
        with self.write_condition:
            self.data = new_data

            # wake all waiting read-events
            for event in self.read_events:
                event.set()  # set all events


class GraphRingBuffer:
    """
    Thread-safe Data Buffer designed to hold some maximum number of rows of data.
    Size determines how many rows of points to keep before overwriting them.
    Size can be changed on the fly.
    <column_names> is a list of names of the columns in the data set
    <size> is the maximum number of points in each data column (rows)
    """

    def __init__(self, column_names, size):
        self.size = size  # max size
        self.length = 0  # number of current elements
        self.tail = 0  # oldest row index
        self.head = 0  # index at which to place the next element

        self.names = column_names
        self.columns = len(column_names)

        self.data = {name: [0] * size for name in column_names}  # initialize each column with size
        self.head_data = {}       # most recent data written
        self.head_data_json = ''  # JSON string of the most recent data written

        # threading locks
        self.lock = Lock()  # write lock
        self.read_events = []  # Event objects

    def move_head(self, n):
        """ Circularly increment head index by n """
        self.head = (self.head + n) % self.size

    def move_tail(self, n):
        """ Circularly increment tail index by n"""
        self.tail = (self.tail + n) % self.size

    def sort(self):
        """ Reorder the data chronologically such that the oldest data point is at index 0 """
        for name, col in self.data.items():
            self.data[name] = col[self.tail:] + col[:self.tail]

    def update(self):
        """ Update all attributes after sort() has been called """
        self.tail = 0
        if self.length < self.size:  # not full
            self.head = self.length
        else:  # full or overfull
            self.head = 0
            self.length = self.size  # this is here for when used in set_size

    def set_size(self, size):
        """ Set the maximum size of the ring """
        if size == self.size:
            return  # no change
        with self.lock:
            self.sort()  # reorder
            if size < self.size:  # decreasing size
                for name, col in self.data.items():
                    extra = self.size - self.length  # extra space after last entry
                    # truncate to get as much recent data as will fit in the new size
                    self.data[name] = col[-size - extra:self.length]
            else:  # increasing size
                for name, col in self.data.items():
                    self.data[name] = self.data[name] + [0] * (size - self.size)  # expand and pad with 0's
            self.size = size
            self.update()

    def get_read_lock(self):
        """ Returns a condition object to be passed into read() and read_all() """
        event = Event()
        event.set()
        self.read_events.append(event)
        return event

    def write(self, data):
        """
        Add data to the ring as a dictionary
        Keys are column names, values are lists of new data for each column.
        New data lists must all be of the same length.
        """
        with self.lock:
            self.head_data = data  # save
            self.head_data_json = json.dumps(data)  # save as json string

            length = len(list(data.values())[0])  # data lists should all be same length
            for i in range(length):  # iterate through indexes of new data list
                for col in self.names:  # for each column
                    self.data[col][self.head] = data[col][i]  # add data point to ring

                self.move_head(1)  # shift head index
                if self.size == self.length:  # if full
                    self.move_tail(1)  # shift tail
                else:  # not full
                    self.length += 1  # tail doesn't move

        for event in self.read_events:
            event.set()  # ready to be read

    def read(self, event, json=False, block=True):
        """
        Return the most recent data element as a JSON string
        <event> is an event object received from get_read_lock(). Acts as a 'ticket' to read the data
        <json> is whether to return the data as a JSON string
        <block> is whether the read blocks before a new write is available.
        """
        if block:
            event.wait()  # continues when new data is available
            event.clear()  # reset event
        else:  # non-blocking
            if event.is_set():  # ready to be read
                event.clear()   # reset event
            else:  # data not ready
                return None
        if json:
            return self.head_data_json
        else:
            return self.head_data

    def read_all(self, event, block=True):
        """ Get all data in the ring as a dictionary """
        if block:
            event.wait()  # triggers when data is available
            event.clear()  # reset event
            self.sort()
            self.update()
            return self.data
        else:  # non-blocking
            if event.is_set():  # ready to be read
                event.clear()   # reset event
                self.sort()
                self.update()
                return self.data
            else:  # data not ready
                return None

