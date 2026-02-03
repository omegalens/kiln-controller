var state = "IDLE";
var state_last = "";
var graph = [ 'profile', 'live'];
var points = [];
var profiles = [];
var time_mode = 0;
var selected_profile = 0;
var selected_profile_name = 'cone-05-long-bisque.json';
var temp_scale = "c";
var time_scale_slope = "s";
var time_scale_profile = "h";
var time_scale_long = "Seconds";
var temp_scale_display = "C";
var kwh_rate = 0.26;
var kw_elements = 9.460;
var currency_type = "EUR";
var last_firing_data = null;

// V2 profile format (rate-based segments)
var profile_version = 1;
var profile_segments = [];
var profile_start_temp = 65;
var use_v2_editor = true;  // Toggle between v1 and v2 editor

// Track actual start temp for graph alignment
var run_actual_start_temp = null;      // Actual kiln temp when run started
var profile_adjusted_for_run = false;  // Has profile graph been adjusted for this run?

var protocol = 'ws:';
if (window.location.protocol == 'https:') {
    protocol = 'wss:';
}
var host = "" + protocol + "//" + window.location.hostname + ":" + window.location.port;

var ws_status = new WebSocket(host+"/status");
var ws_control = new WebSocket(host+"/control");
var ws_config = new WebSocket(host+"/config");
var ws_storage = new WebSocket(host+"/storage");

if(window.webkitRequestAnimationFrame) window.requestAnimationFrame = window.webkitRequestAnimationFrame;

graph.profile =
{
    label: "Profile",
    data: [],
    points: { show: false },
    color: "#75890c",
    draggable: false
};

graph.live =
{
    label: "Live",
    data: [],
    points: { show: false },
    color: "#d8d3c5",
    draggable: false
};


function updateProfile(id)
{
    selected_profile = id;
    selected_profile_name = profiles[id].name;
    
    // For V2 profiles, generate data from segments for display
    var profile = profiles[id];
    var displayData;
    if (isV2Profile(profile)) {
        displayData = segmentsToLegacy(profile.start_temp || 65, profile.segments || []);
    } else {
        displayData = profile.data || [];
    }
    
    var job_seconds = displayData.length === 0 ? 0 : parseInt(displayData[displayData.length-1][0]);
    var kwh = (kw_elements * job_seconds / 3600).toFixed(2);
    var cost =  (kwh*kwh_rate).toFixed(2);
    var job_time = new Date(job_seconds * 1000).toISOString().substr(11, 8);
    $('#sel_prof').html(profiles[id].name);
    $('#sel_prof_eta').html(job_time);
    $('#sel_prof_cost').html(kwh + ' kWh ('+ currency_type +': '+ cost +')');
    graph.profile.data = displayData;
    
    // If there's an active run with an adjusted start temp, re-apply the adjustment
    // This ensures the profile graph stays correct when returning from edit mode
    if (run_actual_start_temp !== null && profile_adjusted_for_run) {
        adjustProfileForActualStartTemp(run_actual_start_temp);
    } else {
        graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ] , getOptions());
    }
}

function deleteProfile()
{
    var profile = { "type": "profile", "data": "", "name": selected_profile_name };
    var delete_struct = { "cmd": "DELETE", "profile": profile };

    var delete_cmd = JSON.stringify(delete_struct);
    console.log("Delete profile:" + selected_profile_name);

    ws_storage.send(delete_cmd);

    ws_storage.send('GET');
    selected_profile_name = profiles[0].name;

    state="IDLE";
    $('#edit').hide();
    $('#profile_selector').show();
    $('#btn_controls').show();
    $('#status').slideDown();
    $('#profile_table').slideUp();
    $('#e2').select2('val', 0);
    graph.profile.points.show = false;
    graph.profile.draggable = false;
    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ], getOptions());
}


function updateProgress(percentage)
{
    if(state=="RUNNING")
    {
        if(percentage > 100) percentage = 100;
        $('#progressBar').css('width', percentage+'%');
        if(percentage>5) $('#progressBar').html(parseInt(percentage)+'%');
    }
    else
    {
        $('#progressBar').css('width', 0+'%');
        $('#progressBar').html('');
    }
}

function updateProfileTable()
{
    var dps = 0;
    var slope = "";
    var color = "";

    var html = '<h3>Schedule Points</h3><div class="table-responsive" style="scroll: none"><table class="table table-striped">';
        html += '<tr><th style="width: 50px">#</th><th>Target Time in ' + time_scale_long+ '</th><th>Target Temperature in °'+temp_scale_display+'</th><th>Slope in &deg;'+temp_scale_display+'/'+time_scale_slope+'</th><th></th></tr>';

    for(var i=0; i<graph.profile.data.length;i++)
    {

        if (i>=1) dps =  ((graph.profile.data[i][1]-graph.profile.data[i-1][1])/(graph.profile.data[i][0]-graph.profile.data[i-1][0]) * 10) / 10;
        if (dps  > 0) { slope = "up";     color="rgba(206, 5, 5, 1)"; } else
        if (dps  < 0) { slope = "down";   color="rgba(23, 108, 204, 1)"; dps *= -1; } else
        if (dps == 0) { slope = "right";  color="grey"; }

        html += '<tr><td><h4>' + (i+1) + '</h4></td>';
        html += '<td><input type="text" class="form-control" id="profiletable-0-'+i+'" value="'+ timeProfileFormatter(graph.profile.data[i][0],true) + '" style="width: 60px" /></td>';
        html += '<td><input type="text" class="form-control" id="profiletable-1-'+i+'" value="'+ graph.profile.data[i][1] + '" style="width: 60px" /></td>';
        html += '<td><div class="input-group"><span class="glyphicon glyphicon-circle-arrow-' + slope + ' input-group-addon ds-trend" style="background: '+color+'"></span><input type="text" class="form-control ds-input" readonly value="' + formatDPS(dps) + '" style="width: 100px" /></div></td>';
        html += '<td>&nbsp;</td></tr>';
    }

    html += '</table></div>';
    html += '<button class="btn btn-default" id="toggle_v2_editor">Switch to Rate Editor</button>';

    $('#profile_table').html(html);
    
    // Handler for switching back to V2 rate-based editor
    $('#toggle_v2_editor').click(function() {
        use_v2_editor = true;
        // Convert legacy data to segments for editing
        profile_segments = legacyToSegments(graph.profile.data);
        profile_start_temp = graph.profile.data && graph.profile.data.length > 0 ? graph.profile.data[0][1] : 65;
        updateProfileTable_v2();
    });

    //Link table to graph
    $(".form-control").change(function(e)
        {
            var id = $(this)[0].id; //e.currentTarget.attributes.id
            var value = parseInt($(this)[0].value);
            var fields = id.split("-");
            var col = parseInt(fields[1]);
            var row = parseInt(fields[2]);

            if (graph.profile.data.length > 0) {
            if (col == 0) {
                graph.profile.data[row][col] = timeProfileFormatter(value,false);
            }
            else {
                graph.profile.data[row][col] = value;
            }

            graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ], getOptions());
            }
            updateProfileTable();

        });
}

function timeProfileFormatter(val, down) {
    var rval = val
    switch(time_scale_profile){
        case "m":
            if (down) {rval = val / 60;} else {rval = val * 60;}
            break;
        case "h":
            if (down) {rval = val / 3600;} else {rval = val * 3600;}
            break;
    }
    return Math.round(rval);
}

// =============================================================================
// V2 Profile (Rate-Based Segments) Editor Functions
// =============================================================================

function isV2Profile(profile) {
    return profile && profile.version && profile.version >= 2;
}

function loadProfileForEditing(profile) {
    if (isV2Profile(profile)) {
        profile_version = 2;
        profile_segments = profile.segments ? JSON.parse(JSON.stringify(profile.segments)) : [];
        profile_start_temp = profile.start_temp || 65;
    } else {
        profile_version = 1;
        // Convert legacy format to segments for editing
        profile_segments = legacyToSegments(profile.data);
        profile_start_temp = profile.data && profile.data.length > 0 ? profile.data[0][1] : 65;
    }
}

function legacyToSegments(data) {
    var segments = [];
    if (!data || data.length < 2) return segments;
    
    for (var i = 1; i < data.length; i++) {
        var prevTime = data[i-1][0];
        var prevTemp = data[i-1][1];
        var currTime = data[i][0];
        var currTemp = data[i][1];
        
        var timeDiff = currTime - prevTime;
        var tempDiff = currTemp - prevTemp;
        
        if (timeDiff > 0) {
            if (tempDiff !== 0) {
                var rate = (tempDiff / timeDiff) * 3600;
                segments.push({rate: Math.round(rate * 10) / 10, target: currTemp, hold: 0});
            } else {
                var holdMinutes = timeDiff / 60;
                if (segments.length > 0 && segments[segments.length - 1].target === currTemp) {
                    segments[segments.length - 1].hold += holdMinutes;
                } else {
                    segments.push({rate: 0, target: currTemp, hold: holdMinutes});
                }
            }
        }
    }
    return segments;
}

function segmentsToLegacy(startTemp, segments) {
    var data = [[0, startTemp]];
    var currentTime = 0;
    var currentTemp = startTemp;
    
    for (var i = 0; i < segments.length; i++) {
        var seg = segments[i];
        var rate = seg.rate;
        
        if (typeof rate === 'string') {
            // Special rates: "max" or "cool"
            var tempDiff = Math.abs(seg.target - currentTemp);
            var estRate = rate === "max" ? 500 : 100;
            var timeSeconds = (tempDiff / estRate) * 3600;
            currentTime += timeSeconds;
            currentTemp = seg.target;
            data.push([Math.round(currentTime), currentTemp]);
        } else if (rate !== 0) {
            // Normal ramp
            var tempDiff = seg.target - currentTemp;
            var timeHours = Math.abs(tempDiff) / Math.abs(rate);
            var timeSeconds = timeHours * 3600;
            currentTime += timeSeconds;
            currentTemp = seg.target;
            data.push([Math.round(currentTime), currentTemp]);
        }
        // Note: rate=0 is pure hold, no ramp point needed
        
        // Add hold
        if (seg.hold > 0) {
            currentTime += seg.hold * 60;
            data.push([Math.round(currentTime), currentTemp]);
        }
    }
    return data;
}

/**
 * Adjust the profile graph line to start from the actual kiln temperature.
 * This makes the "Set" line align with where the kiln actually started,
 * so the actual and set temperature lines overlay properly.
 * 
 * @param {number} actualTemp - The actual kiln temperature when the run started
 */
function adjustProfileForActualStartTemp(actualTemp) {
    if (!profiles[selected_profile]) return;
    
    var profile = profiles[selected_profile];
    var segments = [];
    
    if (isV2Profile(profile)) {
        segments = profile.segments || [];
    } else {
        // Convert legacy profile data to segments
        segments = legacyToSegments(profile.data);
    }
    
    // Regenerate the graph data starting from actual temp
    graph.profile.data = segmentsToLegacy(actualTemp, segments);
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
    
    console.log("Profile graph adjusted to start from actual temp: " + actualTemp);
}

/**
 * Reset the profile graph to use the default start temperature (65°F or profile's start_temp).
 * Called when a run ends to restore the original profile display.
 */
function resetProfileToDefault() {
    run_actual_start_temp = null;
    profile_adjusted_for_run = false;
    
    if (profiles[selected_profile]) {
        updateProfile(selected_profile);
    }
}

function updateProfileTable_v2() {
    var html = '<h3>Firing Segments (Rate-Based)</h3>';
    html += '<div class="form-inline" style="margin-bottom: 10px;">';
    html += '<label>Start Temp: </label> ';
    html += '<input type="text" class="form-control" id="start_temp_input" value="' + profile_start_temp + '" style="width: 80px" />';
    html += ' °' + temp_scale_display;
    html += '</div>';
    
    html += '<div class="table-responsive"><table class="table table-striped">';
    html += '<tr><th>#</th><th>Rate (°' + temp_scale_display + '/hr)</th>';
    html += '<th>Target (°' + temp_scale_display + ')</th>';
    html += '<th>Hold (min)</th><th>Est. Time</th><th></th></tr>';
    
    var cumulative_time = 0;
    var current_temp = profile_start_temp;
    
    for (var i = 0; i < profile_segments.length; i++) {
        var seg = profile_segments[i];
        var seg_time = 0;
        
        // Calculate segment time
        var rate = seg.rate;
        if (typeof rate === 'number' && rate !== 0) {
            var temp_diff = Math.abs(seg.target - current_temp);
            seg_time = (temp_diff / Math.abs(rate)) * 60; // minutes
        } else if (rate === 'max') {
            var temp_diff = Math.abs(seg.target - current_temp);
            seg_time = (temp_diff / 500) * 60;
        } else if (rate === 'cool') {
            var temp_diff = Math.abs(seg.target - current_temp);
            seg_time = (temp_diff / 100) * 60;
        }
        seg_time += seg.hold || 0;
        cumulative_time += seg_time;
        
        var time_str = formatMinutesToHHMM(cumulative_time);
        
        html += '<tr>';
        html += '<td><h4>' + (i + 1) + '</h4></td>';
        html += '<td><input type="text" class="form-control seg-rate" data-idx="' + i + '" ';
        html += 'value="' + seg.rate + '" style="width:80px"/></td>';
        html += '<td><input type="text" class="form-control seg-target" data-idx="' + i + '" ';
        html += 'value="' + seg.target + '" style="width:80px"/></td>';
        html += '<td><input type="text" class="form-control seg-hold" data-idx="' + i + '" ';
        html += 'value="' + (seg.hold || 0) + '" style="width:60px"/></td>';
        html += '<td>' + time_str + '</td>';
        html += '<td><button class="btn btn-danger btn-sm del-segment" data-idx="' + i + '">×</button></td>';
        html += '</tr>';
        
        current_temp = seg.target;
    }
    
    html += '</table></div>';
    html += '<button class="btn btn-success" id="add_segment">+ Add Segment</button>';
    html += ' <button class="btn btn-default" id="toggle_v1_editor">Switch to Point Editor</button>';
    
    $('#profile_table').html(html);
    bindSegmentEvents();
    updateGraphFromSegments();
}

function bindSegmentEvents() {
    // Start temp change
    $('#start_temp_input').change(function() {
        profile_start_temp = parseFloat($(this).val()) || 65;
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
    
    // Segment field changes
    $('.seg-rate, .seg-target, .seg-hold').change(function() {
        var idx = $(this).data('idx');
        var value = $(this).val();
        
        if ($(this).hasClass('seg-rate')) {
            // Handle special rate values
            if (value === 'max' || value === 'cool') {
                profile_segments[idx].rate = value;
            } else {
                profile_segments[idx].rate = parseFloat(value) || 0;
            }
        } else if ($(this).hasClass('seg-target')) {
            profile_segments[idx].target = parseFloat(value) || 0;
        } else if ($(this).hasClass('seg-hold')) {
            profile_segments[idx].hold = parseFloat(value) || 0;
        }
        
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
    
    // Delete segment
    $('.del-segment').click(function() {
        var idx = $(this).data('idx');
        profile_segments.splice(idx, 1);
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
    
    // Add segment
    $('#add_segment').click(function() {
        var last_temp = profile_segments.length > 0 ? 
            profile_segments[profile_segments.length - 1].target : profile_start_temp;
        profile_segments.push({
            rate: 100,
            target: last_temp + 100,
            hold: 0
        });
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
    
    // Toggle to V1 editor
    $('#toggle_v1_editor').click(function() {
        use_v2_editor = false;
        // Convert segments to legacy format
        graph.profile.data = segmentsToLegacy(profile_start_temp, profile_segments);
        updateProfileTable();
    });
}

function updateGraphFromSegments() {
    graph.profile.data = segmentsToLegacy(profile_start_temp, profile_segments);
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
}

function formatMinutesToHHMM(minutes) {
    var hours = Math.floor(minutes / 60);
    var mins = Math.round(minutes % 60);
    return hours + 'h ' + mins + 'm';
}

function formatSecondsToHHMMSS(seconds) {
    var hours = Math.floor(seconds / 3600);
    var mins = Math.floor((seconds % 3600) / 60);
    var secs = Math.floor(seconds % 60);
    return String(hours).padStart(2, '0') + ':' + String(mins).padStart(2, '0') + ':' + String(secs).padStart(2, '0');
}

function formatRateDisplay(rateValue) {
    if (rateValue === 'max') return 'MAX';
    if (rateValue === 'cool') return 'COOL';
    if (rateValue === 0) return 'HOLD';
    if (typeof rateValue === 'number') {
        return clampRate(parseInt(rateValue));
    }
    return '---';
}

function clampRate(rate) {
    if (rate > 9999) return 9999;
    if (rate < -9999) return -9999;
    return rate;
}

function formatDPS(val) {
    var tval = val;
    if (time_scale_slope == "m") {
        tval = val * 60;
    }
    if (time_scale_slope == "h") {
        tval = (val * 60) * 60;
    }
    return Math.round(tval);
}

function hazardTemp(){

    if (temp_scale == "f") {
        return (1500 * 9 / 5) + 32
    }
    else {
        return 1500
    }
}

function timeTickFormatter(val,axis)
{
// hours
if(axis.max>3600) {
  //var hours = Math.floor(val / (3600));
  //return hours;
  return Math.floor(val/3600);
  }

// minutes
if(axis.max<=3600) {
  return Math.floor(val/60);
  }

// seconds
if(axis.max<=60) {
  return val;
  }
}

function runTask()
{
    var cmd =
    {
        "cmd": "RUN",
        "profile": profiles[selected_profile]
    }

    graph.live.data = [];
    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ] , getOptions());

    ws_control.send(JSON.stringify(cmd));

}

function runTaskSimulation()
{
    var cmd =
    {
        "cmd": "SIMULATE",
        "profile": profiles[selected_profile]
    }

    graph.live.data = [];
    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ] , getOptions());

    ws_control.send(JSON.stringify(cmd));

}


function abortTask()
{
    var cmd = {"cmd": "STOP"};
    ws_control.send(JSON.stringify(cmd));
}

function enterNewMode()
{
    // Always use V2 (rate-based) editor for new profiles
    use_v2_editor = true;
    
    state="EDIT"
    $('#status').slideUp();
    $('#edit').show();
    $('#profile_selector').hide();
    $('#btn_controls').hide();
    $('#form_profile_name').attr('value', '');
    $('#form_profile_name').attr('placeholder', 'Please enter a name');
    graph.profile.points.show = true;
    graph.profile.draggable = true;
    graph.profile.data = [];
    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ], getOptions());
    
    // Initialize for V2 editor (always rate-based for new profiles)
    profile_version = 2;
    profile_segments = [];
    profile_start_temp = 65;
    updateProfileTable_v2();
}

function enterEditMode()
{
    state="EDIT"
    $('#status').slideUp();
    $('#edit').show();
    $('#profile_selector').hide();
    $('#btn_controls').hide();
    console.log(profiles);
    $('#form_profile_name').val(profiles[selected_profile].name);
    graph.profile.points.show = true;
    graph.profile.draggable = true;
    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ], getOptions());
    
    // Load profile for editing
    var profile = profiles[selected_profile];
    loadProfileForEditing(profile);
    
    if (use_v2_editor) {
        updateProfileTable_v2();
    } else {
        updateProfileTable();
    }
    toggleTable();
}

function leaveEditMode()
{
    selected_profile_name = $('#form_profile_name').val();
    ws_storage.send('GET');
    state="IDLE";
    $('#edit').hide();
    $('#profile_selector').show();
    $('#btn_controls').show();
    $('#status').slideDown();
    $('#profile_table').slideUp();
    graph.profile.points.show = false;
    graph.profile.draggable = false;
    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ], getOptions());
}

function newPoint()
{
    if(graph.profile.data.length > 0)
    {
        var pointx = parseInt(graph.profile.data[graph.profile.data.length-1][0])+15;
    }
    else
    {
        var pointx = 0;
    }
    graph.profile.data.push([pointx, Math.floor((Math.random()*230)+25)]);
    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ], getOptions());
    updateProfileTable();
}

function delPoint()
{
    graph.profile.data.splice(-1,1)
    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ], getOptions());
    updateProfileTable();
}

function toggleTable()
{
    if($('#profile_table').css('display') == 'none')
    {
        $('#profile_table').slideDown();
    }
    else
    {
        $('#profile_table').slideUp();
    }
}

function saveProfile()
{
    var name = $('#form_profile_name').val();
    
    if (!name || name.trim() === '') {
        $.bootstrapGrowl("<span class=\"glyphicon glyphicon-exclamation-sign\"></span> <b>ERROR:</b><br/>Please enter a profile name", {
            ele: 'body',
            type: 'alert',
            offset: {from: 'top', amount: 250},
            align: 'center',
            width: 385,
            delay: 5000,
            allow_dismiss: true,
            stackup_spacing: 10
        });
        return false;
    }
    
    var profile;
    
    // Save in V2 format if using segment editor
    if (use_v2_editor && profile_segments.length > 0) {
        // Validate segments
        var valid = true;
        var current_temp = profile_start_temp;
        
        for (var i = 0; i < profile_segments.length; i++) {
            var seg = profile_segments[i];
            if (typeof seg.rate === 'number' && seg.rate !== 0) {
                if (seg.rate > 0 && seg.target < current_temp) {
                    valid = false;
                    $.bootstrapGrowl("<span class=\"glyphicon glyphicon-exclamation-sign\"></span> <b>ERROR:</b><br/>Segment " + (i+1) + ": Positive rate with decreasing target", {
                        ele: 'body', type: 'alert', offset: {from: 'top', amount: 250},
                        align: 'center', width: 385, delay: 5000, allow_dismiss: true, stackup_spacing: 10
                    });
                    break;
                }
                if (seg.rate < 0 && seg.target > current_temp) {
                    valid = false;
                    $.bootstrapGrowl("<span class=\"glyphicon glyphicon-exclamation-sign\"></span> <b>ERROR:</b><br/>Segment " + (i+1) + ": Negative rate with increasing target", {
                        ele: 'body', type: 'alert', offset: {from: 'top', amount: 250},
                        align: 'center', width: 385, delay: 5000, allow_dismiss: true, stackup_spacing: 10
                    });
                    break;
                }
            }
            current_temp = seg.target;
        }
        
        if (!valid) return false;
        
        profile = {
            "type": "profile",
            "version": 2,
            "name": name,
            "start_temp": profile_start_temp,
            "temp_units": temp_scale,
            "segments": profile_segments
        };
    } else {
        // Save in V1 format (legacy)
        var rawdata = graph.plot.getData()[0].data;
        var data = [];
        var last = -1;

        for(var i=0; i<rawdata.length; i++)
        {
            if(rawdata[i][0] > last)
            {
                data.push([rawdata[i][0], rawdata[i][1]]);
            }
            else
            {
                $.bootstrapGrowl("<span class=\"glyphicon glyphicon-exclamation-sign\"></span> <b>ERROR 88:</b><br/>An oven is not a time-machine", {
                    ele: 'body',
                    type: 'alert',
                    offset: {from: 'top', amount: 250},
                    align: 'center',
                    width: 385,
                    delay: 5000,
                    allow_dismiss: true,
                    stackup_spacing: 10
                });
                return false;
            }
            last = rawdata[i][0];
        }

        profile = { "type": "profile", "data": data, "name": name };
    }
    
    var put = { "cmd": "PUT", "profile": profile };
    var put_cmd = JSON.stringify(put);
    ws_storage.send(put_cmd);
    leaveEditMode();
}

function get_tick_size() {
//switch(time_scale_profile){
//  case "s":
//    return 1;
//  case "m":
//    return 60;
//  case "h":
//    return 3600;
//  }
return 3600;
}

function getOptions()
{

  var options =
  {

    series:
    {
        lines:
        {
            show: true
        },

        points:
        {
            show: true,
            radius: 5,
            symbol: "circle"
        },

        shadowSize: 3

    },

	xaxis:
    {
      min: 0,
      tickColor: 'rgba(216, 211, 197, 0.2)',
      tickFormatter: timeTickFormatter,
      tickSize: get_tick_size(),
      font:
      {
        size: 14,
        lineHeight: 14,        weight: "normal",
        family: "Digi",
        variant: "small-caps",
        color: "rgba(216, 211, 197, 0.85)"
      }
	},

	yaxis:
    {
      min: 0,
      tickDecimals: 0,
      draggable: false,
      tickColor: 'rgba(216, 211, 197, 0.2)',
      font:
      {
        size: 14,
        lineHeight: 14,
        weight: "normal",
        family: "Digi",
        variant: "small-caps",
        color: "rgba(216, 211, 197, 0.85)"
      }
	},

	grid:
    {
	  color: 'rgba(216, 211, 197, 0.55)',
      borderWidth: 1,
      labelMargin: 10,
      mouseActiveRadius: 50
	},

    legend:
    {
      show: false
    }
  }

  return options;

}

function fetchLastFiring()
{
    $.ajax({
        url: '/api/last_firing',
        type: 'GET',
        dataType: 'json',
        success: function(data) {
            if (data && !data.error) {
                last_firing_data = data;
                displayLastFiring(data);
            } else {
                $('#last_firing_panel').hide();
            }
        },
        error: function() {
            $('#last_firing_panel').hide();
        }
    });
}

function displayLastFiring(data)
{
    if (!data || data.error) {
        $('#last_firing_panel').hide();
        return;
    }

    // Format duration
    var duration_str = new Date(data.duration_seconds * 1000).toISOString().substr(11, 8);
    
    // Format timestamp
    var end_time = new Date(data.end_time);
    var timestamp_str = end_time.toLocaleString();
    
    // Format status with appropriate styling
    var status_str = data.status || 'completed';
    var status_html = '<span class="label label-';
    if (status_str === 'completed') {
        status_html += 'success">Completed';
    } else if (status_str === 'aborted') {
        status_html += 'warning">Aborted';
    } else if (status_str === 'emergency_stop') {
        status_html += 'danger">Emergency Stop';
    } else {
        status_html += 'default">' + status_str;
    }
    status_html += '</span>';
    
    // Update fields
    $('#last_firing_profile').text(data.profile_name || '-');
    $('#last_firing_status').html(status_html);
    $('#last_firing_duration').text(duration_str);
    $('#last_firing_cost').text((data.currency_type || '$') + ' ' + (data.final_cost || 0).toFixed(2));
    $('#last_firing_divergence').text((data.avg_divergence || 0).toFixed(2) + '°' + (data.temp_scale === 'f' ? 'F' : 'C'));
    $('#last_firing_timestamp').text(timestamp_str);
    
    // Show panel only when in IDLE state
    if (state === 'IDLE') {
        $('#last_firing_panel').slideDown();
    }
}

function hideLastFiring()
{
    $('#last_firing_panel').slideUp();
}



$(document).ready(function()
{

    if(!("WebSocket" in window))
    {
        $('#chatLog, input, button, #examples').fadeOut("fast");
        $('<p>Oh no, you need a browser that supports WebSockets. How about <a href="http://www.google.com/chrome">Google Chrome</a>?</p>').appendTo('#container');
    }
    else
    {

        // Status Socket ////////////////////////////////

        ws_status.onopen = function()
        {
            console.log("Status Socket has been opened");

//            $.bootstrapGrowl("<span class=\"glyphicon glyphicon-exclamation-sign\"></span>Getting data from server",
//            {
//            ele: 'body', // which element to append to
//            type: 'success', // (null, 'info', 'error', 'success')
//            offset: {from: 'top', amount: 250}, // 'top', or 'bottom'
//            align: 'center', // ('left', 'right', or 'center')
//            width: 385, // (integer, or 'auto')
//            delay: 2500,
//            allow_dismiss: true,
//            stackup_spacing: 10 // spacing between consecutively stacked growls.
//            });
        };

        ws_status.onclose = function()
        {
            $.bootstrapGrowl("<span class=\"glyphicon glyphicon-exclamation-sign\"></span> <b>ERROR 1:</b><br/>Status Websocket not available", {
            ele: 'body', // which element to append to
            type: 'error', // (null, 'info', 'error', 'success')
            offset: {from: 'top', amount: 250}, // 'top', or 'bottom'
            align: 'center', // ('left', 'right', or 'center')
            width: 385, // (integer, or 'auto')
            delay: 5000,
            allow_dismiss: true,
            stackup_spacing: 10 // spacing between consecutively stacked growls.
          });
        };

        ws_status.onmessage = function(e)
        {
            x = JSON.parse(e.data);
            if (x.type == "backlog")
            {
                if (x.profile)
                {
                    selected_profile_name = x.profile.name;
                    
                    // Find the profile in our list
                    $.each(profiles,  function(i,v) {
                        if(v.name == x.profile.name) {
                            selected_profile = i;
                            $('#e2').select2('val', i);
                            $('#sel_prof').html(v.name);
                        }
                    });
                    
                    // Get the actual starting temperature from the first log entry
                    // and adjust the profile graph to start from there
                    if (x.log && x.log.length > 0) {
                        var firstLogTemp = x.log[0].temperature;
                        run_actual_start_temp = firstLogTemp;
                        profile_adjusted_for_run = true;
                        adjustProfileForActualStartTemp(firstLogTemp);
                        console.log("Backlog: adjusted profile to start from " + firstLogTemp);
                    } else if (x.profile.data && x.profile.data.length > 0) {
                        // Fallback: use profile data from backend if no log yet
                        graph.profile.data = x.profile.data;
                    }
                }

                $.each(x.log, function(i,v) {
                    // Use actual_elapsed_time (wall clock) for x-axis, not runtime (which includes seek offset)
                    var xTime = (typeof v.actual_elapsed_time !== 'undefined') ? v.actual_elapsed_time : v.runtime;
                    graph.live.data.push([xTime, v.temperature]);
                    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ] , getOptions());
                });
            }

            if(state!="EDIT")
            {
                state = x.state;
                if (state!=state_last)
                {
                    // Transition TO RUNNING: capture actual start temp and adjust profile graph
                    if(state == "RUNNING" && state_last != "RUNNING" && state_last != "PAUSED")
                    {
                        // Capture the actual kiln temperature as the starting point
                        run_actual_start_temp = x.temperature;
                        profile_adjusted_for_run = false;
                        console.log("Run starting, actual kiln temp: " + run_actual_start_temp);
                    }
                    
                    // Transition FROM RUNNING to non-running state (except PAUSED)
                    if(state_last == "RUNNING" && state != "PAUSED" )
                    {
                        console.log(state);
                        $('#target_temp').html('---');
                        $('#heat_rate_actual').html('---');
                        $('#heat_rate_set').html('---');
                        $('#elapsed_time').html('--:--:--');
                        updateProgress(0);
                        $.bootstrapGrowl("<span class=\"glyphicon glyphicon-exclamation-sign\"></span> <b>Run completed</b>", {
                        ele: 'body', // which element to append to
                        type: 'success', // (null, 'info', 'error', 'success')
                        offset: {from: 'top', amount: 250}, // 'top', or 'bottom'
                        align: 'center', // ('left', 'right', or 'center')
                        width: 385, // (integer, or 'auto')
                        delay: 0,
                        allow_dismiss: true,
                        stackup_spacing: 10 // spacing between consecutively stacked growls.
                        });
                        // Fetch and display last firing results
                        setTimeout(fetchLastFiring, 1000);
                        
                        // Reset profile graph to default display (65°F or profile start_temp)
                        resetProfileToDefault();
                    }
                }

                if(state=="RUNNING")
                {
                    $("#nav_start").hide();
                    $("#nav_stop").show();
                    hideLastFiring();  // Hide last firing panel while running

                    // Adjust profile graph to start from actual kiln temp (once per run)
                    if (!profile_adjusted_for_run && run_actual_start_temp !== null) {
                        adjustProfileForActualStartTemp(run_actual_start_temp);
                        profile_adjusted_for_run = true;
                    }

                    // Use actual_elapsed_time (wall clock) for x-axis, not runtime (which includes seek offset)
                    var xTime = (typeof x.actual_elapsed_time !== 'undefined') ? x.actual_elapsed_time : x.runtime;
                    graph.live.data.push([xTime, x.temperature]);
                    graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ] , getOptions());

                    // --- Common: Actual rate (always available) ---
                    var actualRate = clampRate(parseInt(x.heat_rate) || 0);
                    $('#heat_rate_actual').html(actualRate);

                    // Check if using segment-based control (v2)
                    if (typeof x.current_segment !== 'undefined' && typeof x.progress !== 'undefined') {
                        // V2 segment-based display
                        
                        // Set rate
                        $('#heat_rate_set').html(formatRateDisplay(x.target_heat_rate));
                        
                        // Elapsed time (wall clock)
                        var elapsed = x.actual_elapsed_time || 0;
                        $('#elapsed_time').html(formatSecondsToHHMMSS(elapsed));
                        
                        // ETA
                        var eta = formatSecondsToHHMMSS(x.eta_seconds || 0);
                        
                        // Progress
                        updateProgress(x.progress);
                        
                        // Segment info in state panel
                        var segmentInfo = 'Seg ' + (x.current_segment + 1) + '/' + (x.total_segments || '?');
                        var phaseInfo = x.segment_phase === 'hold' ? ' (HOLD)' : ' (RAMP)';
                        
                        var stateHtml = '<span class="glyphicon glyphicon-time" style="font-size: 22px; font-weight: normal"></span>';
                        stateHtml += '<span style="font-family: Digi; font-size: 36px;">' + eta + '</span>';
                        stateHtml += '<br/><span style="font-size: 12px;">' + segmentInfo + phaseInfo + '</span>';
                        $('#state').html(stateHtml);
                    } else {
                        // V1 time-based display (legacy)
                        
                        // Set rate: not available for v1
                        $('#heat_rate_set').html('---');
                        
                        // Elapsed time (schedule time)
                        $('#elapsed_time').html(formatSecondsToHHMMSS(x.runtime || 0));
                        
                        // ETA
                        var left = parseInt(x.totaltime - x.runtime);
                        var eta = formatSecondsToHHMMSS(left);
                        
                        // Progress
                        updateProgress(parseFloat(x.runtime) / parseFloat(x.totaltime) * 100);
                        
                        // State display
                        $('#state').html('<span class="glyphicon glyphicon-time" style="font-size: 22px; font-weight: normal"></span><span style="font-family: Digi; font-size: 40px;">' + eta + '</span>');
                    }
                    
                    $('#target_temp').html(parseInt(x.target));
                    $('#cost').html(x.currency_type + parseFloat(x.cost).toFixed(2));
                }
                else
                {
                    $("#nav_start").show();
                    $("#nav_stop").hide();
                    
                    // Reset rate and time displays when not running
                    var actualRate = clampRate(parseInt(x.heat_rate) || 0);
                    $('#heat_rate_actual').html(actualRate);
                    $('#heat_rate_set').html('---');
                    $('#elapsed_time').html('--:--:--');
                    
                    // Handle cooling estimate display inline with state
                    var stateHtml = '<p class="ds-text">'+state+'</p>';
                    if(x.cooling_estimate) {
                        var coolingText = '';
                        if(x.cooling_estimate === 'Ready' || x.cooling_estimate === 'Calculating...') {
                            coolingText = x.cooling_estimate;
                        } else {
                            coolingText = x.cooling_estimate + ' time to 100°F';
                        }
                        stateHtml = '<p class="ds-text">'+state+'<span id="cooling_estimate" style="display:inline; margin-left: 10px; font-size: 0.8em;">' + coolingText + '</span></p>';
                    }
                    $('#state').html(stateHtml);
                    
                    // Show last firing panel when idle
                    if(state == "IDLE" && last_firing_data) {
                        displayLastFiring(last_firing_data);
                    }
                }

                $('#act_temp').html(parseInt(x.temperature));
                if (typeof x.pidstats !== 'undefined') {
                    $('#heat').html('<div class="bar" style="height:'+x.pidstats.out*70+'%;"></div>')
                    }
                if (x.cool > 0.5) { $('#cool').addClass("ds-led-cool-active"); } else { $('#cool').removeClass("ds-led-cool-active"); }
                if (x.air > 0.5) { $('#air').addClass("ds-led-air-active"); } else { $('#air').removeClass("ds-led-air-active"); }
                if (x.temperature > hazardTemp()) { $('#hazard').addClass("ds-led-hazard-active"); } else { $('#hazard').removeClass("ds-led-hazard-active"); }
                if ((x.door == "OPEN") || (x.door == "UNKNOWN")) { $('#door').addClass("ds-led-door-open"); } else { $('#door').removeClass("ds-led-door-open"); }

                state_last = state;

            }
        };

        // Config Socket /////////////////////////////////

        ws_config.onopen = function()
        {
            ws_config.send('GET');
        };

        ws_config.onmessage = function(e)
        {
            console.log (e.data);
            x = JSON.parse(e.data);
            temp_scale = x.temp_scale;
            time_scale_slope = x.time_scale_slope;
            time_scale_profile = x.time_scale_profile;
            kwh_rate = x.kwh_rate;
            kw_elements = x.kw_elements;
            currency_type = x.currency_type;

            if (temp_scale == "c") {temp_scale_display = "C";} else {temp_scale_display = "F";}


            $('#act_temp_scale').html('º'+temp_scale_display);
            $('#target_temp_scale').html('º'+temp_scale_display);
            $('#heat_rate_temp_scale').html('º'+temp_scale_display+'/hr');
            // Note: Set rate unit is just "/hr" since it can be MAX/COOL/HOLD

            switch(time_scale_profile){
                case "s":
                    time_scale_long = "Seconds";
                    break;
                case "m":
                    time_scale_long = "Minutes";
                    break;
                case "h":
                    time_scale_long = "Hours";
                    break;
            }

        }

        // Control Socket ////////////////////////////////

        ws_control.onopen = function()
        {

        };

        ws_control.onmessage = function(e)
        {
            //Data from Simulation
            console.log ("control socket has been opened")
            console.log (e.data);
            x = JSON.parse(e.data);
            // Use actual_elapsed_time (wall clock) for x-axis, not runtime (which includes seek offset)
            var xTime = (typeof x.actual_elapsed_time !== 'undefined') ? x.actual_elapsed_time : x.runtime;
            graph.live.data.push([xTime, x.temperature]);
            graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ] , getOptions());

        }

        // Storage Socket ///////////////////////////////

        ws_storage.onopen = function()
        {
            ws_storage.send('GET');
        };


        ws_storage.onmessage = function(e)
        {
            message = JSON.parse(e.data);

            if(message.resp)
            {
                if(message.resp == "FAIL")
                {
                    if (confirm('Overwrite?'))
                    {
                        message.force=true;
                        console.log("Sending: " + JSON.stringify(message));
                        ws_storage.send(JSON.stringify(message));
                    }
                    else
                    {
                        //do nothing
                    }
                }

                return;
            }

            //the message is an array of profiles
            //FIXME: this should be better, maybe a {"profiles": ...} container?
            profiles = message;
            //delete old options in select
            $('#e2').find('option').remove().end();
            // check if current selected value is a valid profile name
            // if not, update with first available profile name
            var valid_profile_names = profiles.map(function(a) {return a.name;});
            if (
              valid_profile_names.length > 0 &&
              $.inArray(selected_profile_name, valid_profile_names) === -1
            ) {
              selected_profile = 0;
              selected_profile_name = valid_profile_names[0];
            }

            // fill select with new options from websocket
            for (var i=0; i<profiles.length; i++)
            {
                var profile = profiles[i];
                //console.log(profile.name);
                $('#e2').append('<option value="'+i+'">'+profile.name+'</option>');

                if (profile.name == selected_profile_name)
                {
                    selected_profile = i;
                    $('#e2').select2('val', i);
                    updateProfile(i);
                }
            }
        };


        $("#e2").select2(
        {
            placeholder: "Select Profile",
            allowClear: true,
            minimumResultsForSearch: -1
        });


        $("#e2").on("change", function(e)
        {
            updateProfile(e.val);
        });

        // Fetch last firing data on page load
        fetchLastFiring();

    }
});
