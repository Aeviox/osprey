function log(msg) {
    // logs a message to console and to the log div
    console.log(msg)
    $('.logs p').prepend(`> ${msg}<br>`);
}

function error(msg) {
    // logs an error message to console and to the log div
    console.log(msg)
    $('.logs p').prepend(`> <span style="color:red;font-weight:bold;">${msg}</span><br>`);
}

function set_button(name, props) {
    // props is an object that contains properties for the command button named <name>
    if (props.hidden !== undefined) {
        $('button.command.'+name).prop('hidden', props.hidden);
    }
    if (props.disabled !== undefined) {
        $('button.command.'+name).prop('disabled', props.disabled);
    }
    if (props.text !== undefined) {
        $('button.command.'+name).prop('title', props.text);
    }
}

function get_button(name) {
    // returns false if hidden or disabled. Otherwise true.
    var button = $('button.command.'+name);
    console.log("BUTTON:")
    console.log(button.prop('hidden'), button.prop('disabled'))
    if (button.prop('hidden') || button.prop('disabled')) {
        console.log("FALSE")
        return false;
    } else {
        console.log("TRUE")
        return true;
    }
}

$(document).ready(function() {
    var namespace = '/browser';  // namespace for talking with server
    var socket = io(namespace);
    var selected_file = ""  // currently selected file

    socket.on('connect', function() {
        log("SocketIO connected to server");
    });

    socket.on('disconnect', function() {
        log("SocketIO disconnected from server");
    });

    socket.on('log', function(msg) {
        log(msg);
    });

    socket.on('error', function(msg) {
        error(msg)
    });

    // intermittent updates initiated by the server or a manual refresh
    socket.on('update_pages', function(data) {
        // data is a list of objects with info on each stream
        $('.streams ul').empty()
        data.forEach(function(info) {
            $('.streams ul').append(`<li><a href='/stream?group=${info['name']}'>${info['name']}</a></li>`);
        });
    });

    socket.on('update_files', function(data) {
        // data is a list of file names
        selected_file = ""  // clear selected file
        $('.files ul').empty()
        data.forEach(function(filename) {
            $('div.files ul').append(`<li>${filename}</li>`);
        });

        // disable all file buttons
        set_button('load', {disabled: true});
        set_button('rename', {disabled: true});
        set_button('delete', {disabled: true});

        // each file name will set the selected_file variable with its own filename
        $('div.files li').on('click', function(event) {
            $('div.files li.selected').removeClass('selected');  // unset selected form prev
            $(event.target).addClass('selected')  // set selected
            selected_file = $(event.target).text();  // set currently selected file
            set_button('rename', {disabled: false});
            set_button('delete', {disabled: false});
            set_button('load', {disabled: false});
        });
    });

    socket.on('update_buttons', function(data) {
        // data is an object. Each member is a button name with values as the buttons properties
        for (button in data) {
            set_button(button, data[button]);
        };
    });

    socket.on('update_header', function(data) {
        // receive new header text to display for streams
        $('div.container > div.streams > h2').text(data);
    });


    // Constant updates polled every second
    // Todo: Ideally, this socket would just join a room with ID equal to the session ID.
    // Todo: Then the server could just broadcast session-specific updates every second, with no need for polling.
    // Todo: However, I have no idea how to get the session ID on this client side socket.
    // Todo: This is because Flask uses HTTPOnly cookies to store the session ID.
    // Todo: So instead, for now each separate socket just polls the server, and the server checks which session it's coming from.
    setInterval(function() {
        socket.emit('status');
    }, 1000);

    socket.on('status', function(data) {
        // data.save: Time since last database save (string)
        // data.streaming: Database streaming status (string)
        $("div.streams div.streaming").html("Streaming: " + data.streaming);
        $("div.streams div.save").html(     "Last Save: " + data.save);
    });


    var rename_dialog = $('.rename_dialog').dialog({
        autoOpen: false,
        modal: true,
        buttons: {
            "Ok": function() {
                socket.emit('rename', {filename: selected_file, newname: $('#file_name').val()})
                $(this).dialog("close");
            },
            "Cancel": function() {
                $(this).dialog("close");
            }
        },
        close: function() {
            $('.rename_dialog > form')[0].reset();
        }
    });

    var delete_dialog = $('.confirm_dialog').dialog({
        autoOpen: false,
        modal: true,
        'title': 'Delete saved database file?',
        buttons: {
            "Ok": function() {
                socket.emit('delete', selected_file)
                $(this).dialog("close");
            },
            "Cancel": function() {
                $(this).dialog("close");
            }
        }
    });


    // each command button emits an event to the server
    $('div.stream_commands button.command').on('click', function(event) {
        socket.emit(event.target.value);
    });

    $('div.file_commands button.load').on('click', function(event) {
        socket.emit(event.target.value, selected_file);
    });

    $('div.file_commands button.rename').on("click", function() {
        rename_dialog.dialog("open");
    });

    $('div.file_commands button.delete').on('click', function(event) {
        delete_dialog.dialog("open");
    });
});