function log(msg) {
    // logs a message to console and to the log div
    console.log(msg)
    $('.logs > p').prepend(`> ${msg}<br>`);
}

function error(msg) {
    // logs an error message to console and to the log div
    console.log(msg)
    $('.logs > p').prepend(`> <span style="color:red;font-weight:bold;">${msg}</span><br>`);
}

function update_button(name, hidden=null, disabled=null, text=null) {
    console.log(name, hidden, disabled, text)
    if (hidden) {
        $('button.command.'+name).prop('hidden', hidden);
    }
    if (disabled) {
        $('button.command.'+name).prop('disabled', disabled);
    }
    if (text) {
        $('button.command.'+name).prop('text', text);
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

    socket.on('update_pages', function(data) {
        // data is a list of dictionaries with info on each stream
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
        update_button('load', disabled=true);
        update_button('rename', disabled=true);
        update_button('delete', disabled=true);

        // each file name will set the selected_file variable with its own filename
        $('div.files li').on('click', function(event) {
            $("div.files li.selected").removeClass('selected');  // unset selected form prev
            $(event.target).addClass('selected')  // set selected
            selected_file = $(event.target).text();  // set currently selected file
            update_button('load', disabled=false);
            update_button('rename', disabled=false);
            update_button('delete', disabled=false);
        });
    });

    socket.on('update_button', function(data) {
        // data is an object with attributes representing the state of a button
        update_button(data.name, data.hidden, data.disabled, data.text);
    });

    socket.on('update_header', function(data) {
        // receive new header text to display for streams
        $('div.container > div.streams > h2').text(data);
    })


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