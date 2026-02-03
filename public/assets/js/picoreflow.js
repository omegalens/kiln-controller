// =============================================================================
// Kiln Controller - Main JavaScript
// Dark theme dashboard with modular widgets
// =============================================================================

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
var use_v2_editor = true;

// Track actual start temp for graph alignment
var run_actual_start_temp = null;
var profile_adjusted_for_run = false;

// WebSocket setup
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

// Graph series configuration - dark theme colors
graph.profile = {
    label: "Profile",
    data: [],
    points: { show: false },
    color: "#27ae60",  // Green for target/setpoint
    draggable: false,
    lines: { lineWidth: 2 }
};

graph.live = {
    label: "Live",
    data: [],
    points: { show: false },
    color: "#e67e22",  // Orange for actual
    draggable: false,
    lines: { lineWidth: 2 }
};

// =============================================================================
// Temperature Color & Duration Width Helpers
// =============================================================================

/**
 * Get color for temperature bar based on peak temp (°F)
 * Very Low: < 1200°F → blue
 * Low: 1900°F → yellow
 * Mid: 2180°F → orange
 * High: 2400°F → red
 */
function getTempColor(tempF) {
    var stops = [
        { temp: 1200, color: [52, 152, 219] },   // blue
        { temp: 1900, color: [241, 196, 15] },   // yellow
        { temp: 2180, color: [230, 126, 34] },   // orange
        { temp: 2400, color: [231, 76, 60] }     // red
    ];
    
    if (tempF <= stops[0].temp) return 'rgb(' + stops[0].color.join(',') + ')';
    if (tempF >= stops[3].temp) return 'rgb(' + stops[3].color.join(',') + ')';
    
    for (var i = 0; i < stops.length - 1; i++) {
        if (tempF >= stops[i].temp && tempF <= stops[i + 1].temp) {
            var ratio = (tempF - stops[i].temp) / (stops[i + 1].temp - stops[i].temp);
            var r = Math.round(stops[i].color[0] + (stops[i + 1].color[0] - stops[i].color[0]) * ratio);
            var g = Math.round(stops[i].color[1] + (stops[i + 1].color[1] - stops[i].color[1]) * ratio);
            var b = Math.round(stops[i].color[2] + (stops[i + 1].color[2] - stops[i].color[2]) * ratio);
            return 'rgb(' + r + ',' + g + ',' + b + ')';
        }
    }
    return 'rgb(' + stops[2].color.join(',') + ')';
}

/**
 * Get bar width percentage based on duration
 * 3h = 20% (minimum visible), 18h = 100%
 */
function getDurationWidth(durationHours) {
    var minHours = 3;
    var maxHours = 18;
    var minWidth = 20;
    var maxWidth = 100;
    
    var clamped = Math.max(minHours, Math.min(maxHours, durationHours));
    return ((clamped - minHours) / (maxHours - minHours)) * (maxWidth - minWidth) + minWidth;
}

/**
 * Get peak temperature from profile
 */
function getProfilePeakTemp(profile) {
    var peakTemp = 0;
    
    if (isV2Profile(profile)) {
        var segments = profile.segments || [];
        for (var i = 0; i < segments.length; i++) {
            if (segments[i].target > peakTemp) {
                peakTemp = segments[i].target;
            }
        }
    } else {
        var data = profile.data || [];
        for (var i = 0; i < data.length; i++) {
            if (data[i][1] > peakTemp) {
                peakTemp = data[i][1];
            }
        }
    }
    
    return peakTemp;
}

/**
 * Get duration in hours from profile
 */
function getProfileDuration(profile) {
    var displayData;
    if (isV2Profile(profile)) {
        displayData = segmentsToLegacy(profile.start_temp || 65, profile.segments || []);
    } else {
        displayData = profile.data || [];
    }
    
    if (displayData.length === 0) return 0;
    var totalSeconds = displayData[displayData.length - 1][0];
    return totalSeconds / 3600;
}

// =============================================================================
// Profile List Rendering
// =============================================================================

function renderProfileList() {
    var $list = $('#profile-list');
    $list.empty();
    
    for (var i = 0; i < profiles.length; i++) {
        var profile = profiles[i];
        var peakTemp = getProfilePeakTemp(profile);
        var durationHours = getProfileDuration(profile);
        var tempColor = getTempColor(peakTemp);
        var barWidth = getDurationWidth(durationHours);
        
        var isSelected = (i === selected_profile);
        var itemClass = 'profile-item' + (isSelected ? ' selected' : '');
        
        var html = '<li class="' + itemClass + '" data-index="' + i + '">';
        html += '<span class="profile-name">' + profile.name + '</span>';
        html += '<div class="profile-bar-container">';
        html += '<div class="profile-bar" style="width: ' + barWidth + '%; background-color: ' + tempColor + ';"></div>';
        html += '</div>';
        html += '</li>';
        
        $list.append(html);
    }
    
    // Bind click events
    $('.profile-item').on('click', function() {
        var index = $(this).data('index');
        selectProfile(index);
    });
}

function selectProfile(index) {
    selected_profile = index;
    selected_profile_name = profiles[index].name;
    
    // Update visual selection
    $('.profile-item').removeClass('selected');
    $('.profile-item[data-index="' + index + '"]').addClass('selected');
    
    updateProfile(index);
}

function updateProfile(id) {
    selected_profile = id;
    selected_profile_name = profiles[id].name;
    
    var profile = profiles[id];
    var displayData;
    if (isV2Profile(profile)) {
        displayData = segmentsToLegacy(profile.start_temp || 65, profile.segments || []);
    } else {
        displayData = profile.data || [];
    }
    
    var job_seconds = displayData.length === 0 ? 0 : parseInt(displayData[displayData.length-1][0]);
    var kwh = (kw_elements * job_seconds / 3600).toFixed(2);
    var cost = (kwh * kwh_rate).toFixed(2);
    var job_time = new Date(job_seconds * 1000).toISOString().substr(11, 8);
    
    $('#sel_prof').html(profiles[id].name);
    $('#sel_prof_eta').html(job_time);
    $('#sel_prof_cost').html(kwh + ' kWh (' + currency_type + ': ' + cost + ')');
    
    graph.profile.data = displayData;
    
    if (run_actual_start_temp !== null && profile_adjusted_for_run) {
        adjustProfileForActualStartTemp(run_actual_start_temp);
    } else {
        graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
    }
}

// =============================================================================
// Progress Bar
// =============================================================================

function updateProgress(percentage) {
    if (state == "RUNNING") {
        if (percentage > 100) percentage = 100;
        $('#progressBar').css('width', percentage + '%');
        $('#progress-text').text(parseInt(percentage) + '%');
    } else {
        $('#progressBar').css('width', '0%');
        $('#progress-text').text('0%');
    }
}

// =============================================================================
// Profile Table (V1 - Legacy)
// =============================================================================

function updateProfileTable() {
    var dps = 0;
    var slope = "";
    var color = "";

    var html = '<h3 class="text-secondary mt-md">Schedule Points</h3>';
    html += '<div class="table-responsive"><table class="segment-table">';
    html += '<tr><th>#</th><th>Time (' + time_scale_long + ')</th><th>Temp (°' + temp_scale_display + ')</th><th>Rate</th><th></th></tr>';

    for (var i = 0; i < graph.profile.data.length; i++) {
        if (i >= 1) dps = ((graph.profile.data[i][1] - graph.profile.data[i-1][1]) / (graph.profile.data[i][0] - graph.profile.data[i-1][0]) * 10) / 10;
        
        if (dps > 0) { slope = "↑"; color = "var(--accent-danger)"; }
        else if (dps < 0) { slope = "↓"; color = "var(--led-cool)"; dps *= -1; }
        else { slope = "→"; color = "var(--text-muted)"; }

        html += '<tr>';
        html += '<td>' + (i + 1) + '</td>';
        html += '<td><input type="text" class="form-input form-input-sm" id="profiletable-0-' + i + '" value="' + timeProfileFormatter(graph.profile.data[i][0], true) + '" /></td>';
        html += '<td><input type="text" class="form-input form-input-sm" id="profiletable-1-' + i + '" value="' + graph.profile.data[i][1] + '" /></td>';
        html += '<td><span style="color: ' + color + '">' + slope + ' ' + formatDPS(dps) + '</span></td>';
        html += '<td></td></tr>';
    }

    html += '</table></div>';
    html += '<button class="btn btn-secondary mt-md" id="toggle_v2_editor">Switch to Rate Editor</button>';

    $('#profile_table').html(html);
    
    $('#toggle_v2_editor').click(function() {
        use_v2_editor = true;
        profile_segments = legacyToSegments(graph.profile.data);
        profile_start_temp = graph.profile.data && graph.profile.data.length > 0 ? graph.profile.data[0][1] : 65;
        updateProfileTable_v2();
    });

    $(".form-input").change(function(e) {
        var id = $(this)[0].id;
        var value = parseInt($(this)[0].value);
        var fields = id.split("-");
        var col = parseInt(fields[1]);
        var row = parseInt(fields[2]);

        if (graph.profile.data.length > 0) {
            if (col == 0) {
                graph.profile.data[row][col] = timeProfileFormatter(value, false);
            } else {
                graph.profile.data[row][col] = value;
            }
            graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
        }
        updateProfileTable();
    });
}

function timeProfileFormatter(val, down) {
    var rval = val;
    switch (time_scale_profile) {
        case "m":
            if (down) { rval = val / 60; } else { rval = val * 60; }
            break;
        case "h":
            if (down) { rval = val / 3600; } else { rval = val * 3600; }
            break;
    }
    return Math.round(rval);
}

// =============================================================================
// V2 Profile (Rate-Based Segments) Functions
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
            var tempDiff = Math.abs(seg.target - currentTemp);
            var estRate = rate === "max" ? 500 : 100;
            var timeSeconds = (tempDiff / estRate) * 3600;
            currentTime += timeSeconds;
            currentTemp = seg.target;
            data.push([Math.round(currentTime), currentTemp]);
        } else if (rate !== 0) {
            var tempDiff = seg.target - currentTemp;
            var timeHours = Math.abs(tempDiff) / Math.abs(rate);
            var timeSeconds = timeHours * 3600;
            currentTime += timeSeconds;
            currentTemp = seg.target;
            data.push([Math.round(currentTime), currentTemp]);
        }
        
        if (seg.hold > 0) {
            currentTime += seg.hold * 60;
            data.push([Math.round(currentTime), currentTemp]);
        }
    }
    return data;
}

function adjustProfileForActualStartTemp(actualTemp) {
    if (!profiles[selected_profile]) return;
    
    var profile = profiles[selected_profile];
    var segments = [];
    
    if (isV2Profile(profile)) {
        segments = profile.segments || [];
    } else {
        segments = legacyToSegments(profile.data);
    }
    
    graph.profile.data = segmentsToLegacy(actualTemp, segments);
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
    console.log("Profile graph adjusted to start from actual temp: " + actualTemp);
}

function resetProfileToDefault() {
    run_actual_start_temp = null;
    profile_adjusted_for_run = false;
    
    if (profiles[selected_profile]) {
        updateProfile(selected_profile);
    }
}

function updateProfileTable_v2() {
    var html = '<h3 class="text-secondary mt-md">Firing Segments</h3>';
    html += '<div class="form-group">';
    html += '<label class="form-label">Start Temp</label>';
    html += '<input type="text" class="form-input form-input-sm" id="start_temp_input" value="' + profile_start_temp + '" style="width: 100px" />';
    html += ' °' + temp_scale_display;
    html += '</div>';
    
    html += '<div class="table-responsive"><table class="segment-table">';
    html += '<tr><th>#</th><th>Rate (°' + temp_scale_display + '/hr)</th>';
    html += '<th>Target (°' + temp_scale_display + ')</th>';
    html += '<th>Hold (min)</th><th>Est.</th><th></th></tr>';
    
    var cumulative_time = 0;
    var current_temp = profile_start_temp;
    
    for (var i = 0; i < profile_segments.length; i++) {
        var seg = profile_segments[i];
        var seg_time = 0;
        
        var rate = seg.rate;
        if (typeof rate === 'number' && rate !== 0) {
            var temp_diff = Math.abs(seg.target - current_temp);
            seg_time = (temp_diff / Math.abs(rate)) * 60;
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
        html += '<td>' + (i + 1) + '</td>';
        html += '<td><input type="text" class="form-input form-input-sm seg-rate" data-idx="' + i + '" value="' + seg.rate + '" /></td>';
        html += '<td><input type="text" class="form-input form-input-sm seg-target" data-idx="' + i + '" value="' + seg.target + '" /></td>';
        html += '<td><input type="text" class="form-input form-input-sm seg-hold" data-idx="' + i + '" value="' + (seg.hold || 0) + '" /></td>';
        html += '<td class="text-muted">' + time_str + '</td>';
        html += '<td><button class="btn-delete-segment del-segment" data-idx="' + i + '">×</button></td>';
        html += '</tr>';
        
        current_temp = seg.target;
    }
    
    html += '</table></div>';
    html += '<div class="flex gap-sm mt-md">';
    html += '<button class="btn btn-success" id="add_segment">+ Segment</button>';
    html += '<button class="btn btn-secondary" id="toggle_v1_editor">Point Editor</button>';
    html += '</div>';
    
    $('#profile_table').html(html);
    bindSegmentEvents();
    updateGraphFromSegments();
}

function bindSegmentEvents() {
    $('#start_temp_input').change(function() {
        profile_start_temp = parseFloat($(this).val()) || 65;
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
    
    $('.seg-rate, .seg-target, .seg-hold').change(function() {
        var idx = $(this).data('idx');
        var value = $(this).val();
        
        if ($(this).hasClass('seg-rate')) {
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
    
    $('.del-segment').click(function() {
        var idx = $(this).data('idx');
        profile_segments.splice(idx, 1);
        updateGraphFromSegments();
        updateProfileTable_v2();
    });
    
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
    
    $('#toggle_v1_editor').click(function() {
        use_v2_editor = false;
        graph.profile.data = segmentsToLegacy(profile_start_temp, profile_segments);
        updateProfileTable();
    });
}

function updateGraphFromSegments() {
    graph.profile.data = segmentsToLegacy(profile_start_temp, profile_segments);
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
}

// =============================================================================
// Utility Functions
// =============================================================================

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
    if (time_scale_slope == "m") { tval = val * 60; }
    if (time_scale_slope == "h") { tval = (val * 60) * 60; }
    return Math.round(tval);
}

function hazardTemp() {
    if (temp_scale == "f") {
        return (1500 * 9 / 5) + 32;
    } else {
        return 1500;
    }
}

function timeTickFormatter(val, axis) {
    if (axis.max > 3600) {
        return Math.floor(val / 3600);
    }
    if (axis.max <= 3600) {
        return Math.floor(val / 60);
    }
    if (axis.max <= 60) {
        return val;
    }
}

// =============================================================================
// Run Control
// =============================================================================

function runTask() {
    var cmd = {
        "cmd": "RUN",
        "profile": profiles[selected_profile]
    };

    graph.live.data = [];
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
    ws_control.send(JSON.stringify(cmd));
    
    // Hide confirmation panel
    hideConfirmation();
}

function abortTask() {
    var cmd = {"cmd": "STOP"};
    ws_control.send(JSON.stringify(cmd));
}

// =============================================================================
// Confirmation Panel
// =============================================================================

function showConfirmation() {
    $('#confirm-panel').addClass('visible').show();
}

function hideConfirmation() {
    $('#confirm-panel').removeClass('visible');
    setTimeout(function() {
        if (!$('#confirm-panel').hasClass('visible')) {
            $('#confirm-panel').hide();
        }
    }, 300);
}

// =============================================================================
// Edit Mode
// =============================================================================

function enterNewMode() {
    use_v2_editor = true;
    state = "EDIT";
    
    $('#edit-indicator').show();
    $('#btn_controls').hide();
    $('#edit-panel').slideDown();
    $('#form_profile_name').val('').attr('placeholder', 'Enter profile name');
    
    graph.profile.points.show = true;
    graph.profile.draggable = true;
    graph.profile.data = [];
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
    
    profile_version = 2;
    profile_segments = [];
    profile_start_temp = 65;
    updateProfileTable_v2();
    $('#profile_table').slideDown();
}

function enterEditMode() {
    state = "EDIT";
    
    $('#edit-indicator').show();
    $('#btn_controls').hide();
    $('#edit-panel').slideDown();
    $('#form_profile_name').val(profiles[selected_profile].name);
    
    graph.profile.points.show = true;
    graph.profile.draggable = true;
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
    
    var profile = profiles[selected_profile];
    loadProfileForEditing(profile);
    
    if (use_v2_editor) {
        updateProfileTable_v2();
    } else {
        updateProfileTable();
    }
    $('#profile_table').slideDown();
}

function leaveEditMode() {
    selected_profile_name = $('#form_profile_name').val();
    ws_storage.send('GET');
    state = "IDLE";
    
    $('#edit-indicator').hide();
    $('#edit-panel').slideUp();
    $('#btn_controls').show();
    $('#profile_table').slideUp();
    
    graph.profile.points.show = false;
    graph.profile.draggable = false;
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
}

function newPoint() {
    if (graph.profile.data.length > 0) {
        var pointx = parseInt(graph.profile.data[graph.profile.data.length-1][0]) + 15;
    } else {
        var pointx = 0;
    }
    graph.profile.data.push([pointx, Math.floor((Math.random() * 230) + 25)]);
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
    updateProfileTable();
}

function delPoint() {
    graph.profile.data.splice(-1, 1);
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
    updateProfileTable();
}

function toggleTable() {
    if ($('#profile_table').css('display') == 'none') {
        $('#profile_table').slideDown();
    } else {
        $('#profile_table').slideUp();
    }
}

function deleteProfile() {
    var profile = { "type": "profile", "data": "", "name": selected_profile_name };
    var delete_struct = { "cmd": "DELETE", "profile": profile };
    var delete_cmd = JSON.stringify(delete_struct);
    
    console.log("Delete profile:" + selected_profile_name);
    ws_storage.send(delete_cmd);
    ws_storage.send('GET');
    
    selected_profile_name = profiles[0] ? profiles[0].name : '';
    leaveEditMode();
}

function saveProfile() {
    var name = $('#form_profile_name').val();
    
    if (!name || name.trim() === '') {
        showAlert('error', 'Please enter a profile name');
        return false;
    }
    
    var profile;
    
    if (use_v2_editor && profile_segments.length > 0) {
        var valid = true;
        var current_temp = profile_start_temp;
        
        for (var i = 0; i < profile_segments.length; i++) {
            var seg = profile_segments[i];
            if (typeof seg.rate === 'number' && seg.rate !== 0) {
                if (seg.rate > 0 && seg.target < current_temp) {
                    valid = false;
                    showAlert('error', 'Segment ' + (i+1) + ': Positive rate with decreasing target');
                    break;
                }
                if (seg.rate < 0 && seg.target > current_temp) {
                    valid = false;
                    showAlert('error', 'Segment ' + (i+1) + ': Negative rate with increasing target');
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
        var rawdata = graph.plot.getData()[0].data;
        var data = [];
        var last = -1;

        for (var i = 0; i < rawdata.length; i++) {
            if (rawdata[i][0] > last) {
                data.push([rawdata[i][0], rawdata[i][1]]);
            } else {
                showAlert('error', 'Time points must be in ascending order');
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

// =============================================================================
// Alert System
// =============================================================================

function showAlert(type, message) {
    $.bootstrapGrowl('<b>' + (type === 'error' ? 'ERROR' : 'INFO') + ':</b> ' + message, {
        ele: 'body',
        type: type === 'error' ? 'danger' : type,
        offset: {from: 'top', amount: 80},
        align: 'center',
        width: 350,
        delay: 5000,
        allow_dismiss: true,
        stackup_spacing: 10
    });
}

// =============================================================================
// Graph Options - Dark Theme
// =============================================================================

function get_tick_size() {
    return 3600;
}

function getOptions() {
    var options = {
        series: {
            lines: { show: true, lineWidth: 2 },
            points: { show: true, radius: 4, symbol: "circle" },
            shadowSize: 0
        },
        xaxis: {
            min: 0,
            tickColor: 'rgba(255, 255, 255, 0.1)',
            tickFormatter: timeTickFormatter,
            tickSize: get_tick_size(),
            font: {
                size: 12,
                lineHeight: 14,
                weight: "normal",
                family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
                color: "rgba(255, 255, 255, 0.5)"
            }
        },
        yaxis: {
            min: 0,
            tickDecimals: 0,
            draggable: false,
            tickColor: 'rgba(255, 255, 255, 0.1)',
            font: {
                size: 12,
                lineHeight: 14,
                weight: "normal",
                family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
                color: "rgba(255, 255, 255, 0.5)"
            }
        },
        grid: {
            color: 'rgba(255, 255, 255, 0.15)',
            borderWidth: 0,
            backgroundColor: 'transparent',
            labelMargin: 10,
            mouseActiveRadius: 50
        },
        legend: { show: false }
    };
    return options;
}

// =============================================================================
// Last Firing Results
// =============================================================================

function fetchLastFiring() {
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

function displayLastFiring(data) {
    if (!data || data.error) {
        $('#last_firing_panel').hide();
        return;
    }

    var duration_str = new Date(data.duration_seconds * 1000).toISOString().substr(11, 8);
    var end_time = new Date(data.end_time);
    var timestamp_str = end_time.toLocaleString();
    
    var status_str = data.status || 'completed';
    var status_class = 'completed';
    var status_text = 'Completed';
    
    if (status_str === 'aborted') {
        status_class = 'aborted';
        status_text = 'Aborted';
    } else if (status_str === 'emergency_stop') {
        status_class = 'emergency';
        status_text = 'Emergency Stop';
    }
    
    var status_html = '<span class="status-badge ' + status_class + '">' + status_text + '</span>';
    
    $('#last_firing_profile').text(data.profile_name || '-');
    $('#last_firing_status').html(status_html);
    $('#last_firing_duration').text(duration_str);
    $('#last_firing_cost').text((data.currency_type || '$') + ' ' + (data.final_cost || 0).toFixed(2));
    $('#last_firing_divergence').text((data.avg_divergence || 0).toFixed(2) + '°' + (data.temp_scale === 'f' ? 'F' : 'C'));
    $('#last_firing_timestamp').text(timestamp_str);
    
    // Restore collapsed state from localStorage
    var isCollapsed = localStorage.getItem('lastFiringCollapsed') === 'true';
    if (isCollapsed) {
        $('#last_firing_panel').addClass('collapsed');
    } else {
        $('#last_firing_panel').removeClass('collapsed');
    }
    
    if (state === 'IDLE') {
        $('#last_firing_panel').show();
    }
}

function hideLastFiring() {
    $('#last_firing_panel').hide();
}

// =============================================================================
// Document Ready - Initialize
// =============================================================================

$(document).ready(function() {
    if (!("WebSocket" in window)) {
        $('body').html('<div class="widget" style="margin: 50px auto; max-width: 400px; padding: 40px; text-align: center;"><h2>Browser Not Supported</h2><p>Please use a browser that supports WebSockets, such as <a href="http://www.google.com/chrome">Google Chrome</a>.</p></div>');
        return;
    }

    // ===================
    // Status Socket
    // ===================
    
    ws_status.onopen = function() {
        console.log("Status Socket connected");
    };

    ws_status.onclose = function() {
        showAlert('error', 'Status WebSocket disconnected');
    };

    ws_status.onmessage = function(e) {
        var x = JSON.parse(e.data);
        
        if (x.type == "backlog") {
            if (x.profile) {
                selected_profile_name = x.profile.name;
                
                $.each(profiles, function(i, v) {
                    if (v.name == x.profile.name) {
                        selected_profile = i;
                        $('#sel_prof').html(v.name);
                        renderProfileList();
                    }
                });
                
                if (x.log && x.log.length > 0) {
                    var firstLogTemp = x.log[0].temperature;
                    run_actual_start_temp = firstLogTemp;
                    profile_adjusted_for_run = true;
                    adjustProfileForActualStartTemp(firstLogTemp);
                } else if (x.profile.data && x.profile.data.length > 0) {
                    graph.profile.data = x.profile.data;
                }
            }

            $.each(x.log, function(i, v) {
                var xTime = (typeof v.actual_elapsed_time !== 'undefined') ? v.actual_elapsed_time : v.runtime;
                graph.live.data.push([xTime, v.temperature]);
                graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
            });
        }

        if (state != "EDIT") {
            state = x.state;
            
            if (state != state_last) {
                if (state == "RUNNING" && state_last != "RUNNING" && state_last != "PAUSED") {
                    run_actual_start_temp = x.temperature;
                    profile_adjusted_for_run = false;
                }
                
                if (state_last == "RUNNING" && state != "PAUSED") {
                    $('#target_temp').html('---');
                    $('#graph-target').html('---');
                    $('#heat_rate_actual').html('---');
                    $('#heat_rate_set').html('---');
                    $('#elapsed_time').html('--:--:--');
                    updateProgress(0);
                    showAlert('success', 'Run completed');
                    setTimeout(fetchLastFiring, 1000);
                    resetProfileToDefault();
                }
            }

            // Update state display
            var stateText = state;
            if (x.cooling_estimate && state !== 'RUNNING') {
                if (x.cooling_estimate !== 'Ready' && x.cooling_estimate !== 'Calculating...') {
                    stateText = state + ' - ' + x.cooling_estimate + ' to 100°F';
                }
            }
            $('#state').text(stateText);
            
            // Add running class for styling
            if (state === 'RUNNING') {
                $('#state').addClass('running');
            } else {
                $('#state').removeClass('running');
            }

            if (state == "RUNNING") {
                $("#btn_start").hide();
                $("#btn_stop").show();
                $('#eta-display').show();
                hideLastFiring();

                if (!profile_adjusted_for_run && run_actual_start_temp !== null) {
                    adjustProfileForActualStartTemp(run_actual_start_temp);
                    profile_adjusted_for_run = true;
                }

                var xTime = (typeof x.actual_elapsed_time !== 'undefined') ? x.actual_elapsed_time : x.runtime;
                graph.live.data.push([xTime, x.temperature]);
                graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());

                var actualRate = clampRate(parseInt(x.heat_rate) || 0);
                $('#heat_rate_actual').html(actualRate);

                if (typeof x.current_segment !== 'undefined' && typeof x.progress !== 'undefined') {
                    $('#heat_rate_set').html(formatRateDisplay(x.target_heat_rate));
                    var elapsed = x.actual_elapsed_time || 0;
                    $('#elapsed_time').html(formatSecondsToHHMMSS(elapsed));
                    var eta = formatSecondsToHHMMSS(x.eta_seconds || 0);
                    $('#eta-time').text(eta);
                    updateProgress(x.progress);
                } else {
                    $('#heat_rate_set').html('---');
                    $('#elapsed_time').html(formatSecondsToHHMMSS(x.runtime || 0));
                    var left = parseInt(x.totaltime - x.runtime);
                    var eta = formatSecondsToHHMMSS(left);
                    $('#eta-time').text(eta);
                    updateProgress(parseFloat(x.runtime) / parseFloat(x.totaltime) * 100);
                }
                
                $('#target_temp').html(parseInt(x.target));
                $('#graph-target').html(parseInt(x.target) + '°' + temp_scale_display);
                $('#cost').html(parseFloat(x.cost).toFixed(2));
            } else {
                $("#btn_start").show();
                $("#btn_stop").hide();
                $('#eta-display').hide();
                
                var actualRate = clampRate(parseInt(x.heat_rate) || 0);
                $('#heat_rate_actual').html(actualRate);
                $('#heat_rate_set').html('---');
                $('#elapsed_time').html('--:--:--');
                
                if (state == "IDLE" && last_firing_data) {
                    displayLastFiring(last_firing_data);
                }
            }

            // Update displays
            $('#act_temp').html(parseInt(x.temperature));
            $('#graph-current').html(parseInt(x.temperature) + '°' + temp_scale_display);
            
            // Update graph remaining time
            if (state === 'RUNNING') {
                var remaining = (typeof x.eta_seconds !== 'undefined') ? x.eta_seconds : (x.totaltime - x.runtime);
                $('#graph-remaining').html(formatSecondsToHHMMSS(remaining));
            } else {
                $('#graph-remaining').html('--:--:--');
            }

            // LED indicators - using new class names
            if (x.cool > 0.5) { $('#cool').addClass("led-cool-active"); } else { $('#cool').removeClass("led-cool-active"); }
            if (x.air > 0.5) { $('#air').addClass("led-air-active"); } else { $('#air').removeClass("led-air-active"); }
            if (x.temperature > hazardTemp()) { $('#hazard').addClass("led-hazard-active"); } else { $('#hazard').removeClass("led-hazard-active"); }
            if ((x.door == "OPEN") || (x.door == "UNKNOWN")) { $('#door').addClass("led-door-open"); } else { $('#door').removeClass("led-door-open"); }
            
            // Heat LED with bar indicator
            if (typeof x.pidstats !== 'undefined' && x.pidstats.out > 0) {
                $('#heat').addClass("led-heat-active");
            } else {
                $('#heat').removeClass("led-heat-active");
            }

            state_last = state;
        }
    };

    // ===================
    // Config Socket
    // ===================
    
    ws_config.onopen = function() {
        ws_config.send('GET');
    };

    ws_config.onmessage = function(e) {
        var x = JSON.parse(e.data);
        temp_scale = x.temp_scale;
        time_scale_slope = x.time_scale_slope;
        time_scale_profile = x.time_scale_profile;
        kwh_rate = x.kwh_rate;
        kw_elements = x.kw_elements;
        currency_type = x.currency_type;

        temp_scale_display = (temp_scale == "c") ? "C" : "F";

        $('#act_temp_scale').html('°' + temp_scale_display);
        $('#target_temp_scale').html('°' + temp_scale_display);
        $('#heat_rate_temp_scale').html('°' + temp_scale_display + '/hr');
        $('#currency_display').html(currency_type);

        switch (time_scale_profile) {
            case "s": time_scale_long = "Seconds"; break;
            case "m": time_scale_long = "Minutes"; break;
            case "h": time_scale_long = "Hours"; break;
        }
    };

    // ===================
    // Control Socket
    // ===================
    
    ws_control.onopen = function() {};

    ws_control.onmessage = function(e) {
        var x = JSON.parse(e.data);
        var xTime = (typeof x.actual_elapsed_time !== 'undefined') ? x.actual_elapsed_time : x.runtime;
        graph.live.data.push([xTime, x.temperature]);
        graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
    };

    // ===================
    // Storage Socket
    // ===================
    
    ws_storage.onopen = function() {
        ws_storage.send('GET');
    };

    ws_storage.onmessage = function(e) {
        var message = JSON.parse(e.data);

        if (message.resp) {
            if (message.resp == "FAIL") {
                if (confirm('Profile exists. Overwrite?')) {
                    message.force = true;
                    ws_storage.send(JSON.stringify(message));
                }
            }
            return;
        }

        profiles = message;
        
        var valid_profile_names = profiles.map(function(a) { return a.name; });
        if (valid_profile_names.length > 0 && $.inArray(selected_profile_name, valid_profile_names) === -1) {
            selected_profile = 0;
            selected_profile_name = valid_profile_names[0];
        }

        // Find and select the current profile
        for (var i = 0; i < profiles.length; i++) {
            if (profiles[i].name == selected_profile_name) {
                selected_profile = i;
                updateProfile(i);
                break;
            }
        }
        
        renderProfileList();
    };

    // ===================
    // UI Event Handlers
    // ===================
    
    // Start button - show confirmation
    $('#btn_start').on('click', function() {
        showConfirmation();
    });
    
    // Confirmation actions
    $('#confirm-yes').on('click', function() {
        runTask();
    });
    
    $('#confirm-no').on('click', function() {
        hideConfirmation();
    });
    
    // Stop button
    $('#btn_stop').on('click', function() {
        abortTask();
    });
    
    // Edit button
    $('#btn_edit').on('click', function() {
        enterEditMode();
    });
    
    // New profile button
    $('#btn_new').on('click', function() {
        enterNewMode();
    });
    
    // Edit panel buttons
    $('#btn_save').on('click', function() {
        saveProfile();
    });
    
    $('#btn_exit').on('click', function() {
        leaveEditMode();
    });
    
    $('#btn_delProfile').on('click', function() {
        if (confirm('Delete this profile? This cannot be undone.')) {
            deleteProfile();
        }
    });
    
    $('#btn_newPoint').on('click', function() {
        newPoint();
    });
    
    $('#btn_delPoint').on('click', function() {
        delPoint();
    });
    
    $('#btn_table').on('click', function() {
        toggleTable();
    });
    
    // Last firing toggle
    $('#toggle-last-firing').on('click', function() {
        var $panel = $('#last_firing_panel');
        $panel.toggleClass('collapsed');
        localStorage.setItem('lastFiringCollapsed', $panel.hasClass('collapsed'));
    });

    // Fetch last firing data on load
    fetchLastFiring();
    
    // Initialize graph
    graph.plot = $.plot("#graph_container", [graph.profile, graph.live], getOptions());
});
