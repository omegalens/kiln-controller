// public/assets/js/settings.js
// Settings panel — rendered entirely from the server schema (ledger D112).
// No setting names are hardcoded here.

(function () {
  'use strict';

  var API = '/api/settings';
  var snap = null;                       // last GET /api/settings payload
  var dirty = { global: {}, kiln: {} };  // key -> new raw value
  var resets = { global: {}, kiln: {} }; // key -> true

  function tempUnit() { return snap.values.temp_scale === 'c' ? '°C' : '°F'; }

  function unitText(entry) {
    if (!entry.unit) return '';
    if (entry.unit === 'temp') return tempUnit();
    if (entry.unit === 'temp_per_hr') return tempUnit() + '/hr';
    return entry.unit;
  }

  function displayValue(key) {
    // Pending restart-apply target beats the running value in the form.
    if (Object.prototype.hasOwnProperty.call(snap.pending, key)) return snap.pending[key];
    return snap.values[key];
  }

  function hasDirty() {
    return Object.keys(dirty.global).length + Object.keys(dirty.kiln).length +
           Object.keys(resets.global).length + Object.keys(resets.kiln).length > 0;
  }

  function isLocked(entry) {
    if (snap.oven_state === 'IDLE') return false;
    return !!entry.safety && !entry.mid_firing_editable;
  }

  function controlFor(key, entry) {
    var value = displayValue(key);
    var $input;
    if (entry.type === 'bool') {
      $input = $('<input type="checkbox">').prop('checked', value === true);
    } else if (entry.type === 'enum') {
      $input = $('<select class="form-input">');
      entry.choices.forEach(function (c) {
        $input.append($('<option>').val(c).text(c));
      });
      $input.val(value);
    } else if (entry.type === 'str') {
      $input = $('<input class="form-input">')
        .attr('type', entry.secret ? 'password' : 'text')
        .val(value === null ? '' : value);
    } else {
      $input = $('<input type="number" class="form-input">')
        .attr({ min: entry.min, max: entry.max, step: entry.type === 'int' ? 1 : 'any' })
        .val(value);
    }
    $input.attr('data-key', key);
    if (isLocked(entry)) {
      $input.prop('disabled', true)
        .attr('title', 'Safety setting — locked while the oven is ' + snap.oven_state);
    }
    return $input;
  }

  function readInput($input, entry) {
    if (entry.type === 'bool') return $input.prop('checked');
    if (entry.type === 'str') return $input.val();
    if (entry.type === 'enum') return $input.val();
    var n = parseFloat($input.val());
    return isNaN(n) ? null : n;
  }

  function renderRow(key, entry) {
    var scope = entry.scope;
    var source = snap.sources[key];
    var $row = $('<div class="setting-row">').attr('data-key', key);
    var $info = $('<div class="setting-info">');
    var $label = $('<span class="setting-label">').text(entry.label);
    var $badge = $('<span class="setting-source">');
    if (Object.prototype.hasOwnProperty.call(snap.pending, key)) {
      $badge.addClass('source-pending').text('pending restart');
    } else if (source !== 'default') {
      $badge.addClass('source-' + source).text(source);
    } else {
      $badge = null;
    }
    $info.append($label);
    if ($badge) $info.append($badge);
    $info.append($('<p class="setting-help">').text(entry.help));

    var $control = $('<div class="setting-control">');
    var $input = controlFor(key, entry);
    $control.append($input);
    var unit = unitText(entry);
    if (unit) $control.append($('<span class="setting-unit">').text(unit));
    if (source !== 'default' && !isLocked(entry)) {
      $control.append(
        $('<button type="button" class="setting-reset" title="Reset to default">')
          .text('↺ default')
          .on('click', function () {
            resets[scope][key] = true;
            delete dirty[scope][key];
            $row.addClass('setting-dirty');
            $input.val(entry.type === 'bool' ? undefined : snap.defaults[key]);
            if (entry.type === 'bool') $input.prop('checked', snap.defaults[key]);
            $input.prop('disabled', true);
            updateSaveButtons();
          }));
    }

    $input.on('change input', function () {
      var next = readInput($input, entry);
      var current = displayValue(key);
      if (next === current || (next === '' && current === null)) {
        delete dirty[scope][key];
        $row.removeClass('setting-dirty');
      } else {
        dirty[scope][key] = next;
        $row.addClass('setting-dirty');
      }
      updateSaveButtons();
    });

    $row.append($info, $control);
    return $row;
  }

  function renderScope(scope, $container) {
    $container.empty();
    snap.category_order.forEach(function (category) {
      var keys = Object.keys(snap.schema).filter(function (k) {
        var e = snap.schema[k];
        if (e.scope !== scope || e.category !== category) return false;
        if (e.sim_only && !snap.simulate) return false;
        return true;
      }).sort();
      if (!keys.length) return;
      var $section = $('<div class="settings-category">')
        .append($('<h3>').text(category));
      keys.forEach(function (k) { $section.append(renderRow(k, snap.schema[k])); });
      $container.append($section);
    });
  }

  function renderKilnBar() {
    var idle = snap.oven_state === 'IDLE';
    var $select = $('#kiln-select').empty();
    $select.append($('<option>').val('').text('— defaults (no kiln) —'));
    snap.kilns.forEach(function (name) {
      $select.append($('<option>').val(name).text(name));
    });
    $select.val(snap.active_kiln || '');
    $('#active-kiln-label').text(
      snap.active_kiln ? 'active: ' + snap.active_kiln : 'no kiln active — using defaults');
    $('.kiln-bar button, #kiln-select').prop('disabled', !idle);
    $('#kiln-bar-hint').toggle(!idle || !snap.active_kiln);
    if (!idle) {
      $('#kiln-bar-hint').text('Kiln switching is locked while the oven is ' + snap.oven_state + '.');
    } else if (!snap.active_kiln) {
      $('#kiln-bar-hint').text('Create and activate a kiln settings profile to edit kiln settings.');
    }
    // Kiln-scope inputs are read-only without an active kiln (nothing to
    // save to). Only ever ADD disabling here — per-input safety locks were
    // already applied at render time and must not be un-done.
    if (!snap.active_kiln) {
      $('#kiln-settings').find('input, select, button').prop('disabled', true);
    }
  }

  function updateSaveButtons() {
    $('#btn_save_kiln').prop('disabled',
      !Object.keys(dirty.kiln).length && !Object.keys(resets.kiln).length);
    $('#btn_save_global').prop('disabled',
      !Object.keys(dirty.global).length && !Object.keys(resets.global).length);
  }

  function renderAll() {
    dirty = { global: {}, kiln: {} };
    resets = { global: {}, kiln: {} };
    $('#oven-state-chip').text(snap.oven_state);
    renderScope('kiln', $('#kiln-settings'));
    renderScope('global', $('#global-settings'));
    renderKilnBar();
    $('#restart-banner').toggle(snap.restart_pending);
    updateSaveButtons();
  }

  function refresh() {
    return $.getJSON(API).done(function (data) {
      snap = data;
      renderAll();
    }).fail(function () {
      $.bootstrapGrowl('Could not load settings', { type: 'danger' });
    });
  }

  function post(url, payload) {
    return $.ajax({
      url: url, type: 'POST', contentType: 'application/json',
      data: JSON.stringify(payload)
    });
  }

  // --- kiln bar actions -------------------------------------------------
  function bindKilnBar() {
    $('#btn_kiln_new').on('click', function () {
      var name = window.prompt('Name for the new kiln settings profile:');
      if (!name) return;
      post(API + '/kilns', { name: name }).done(refresh).fail(showErrors);
    });
    $('#btn_kiln_dup').on('click', function () {
      var source = $('#kiln-select').val();
      if (!source) { $.bootstrapGrowl('Select a kiln to duplicate', { type: 'warning' }); return; }
      var name = window.prompt('Name for the copy of "' + source + '":');
      if (!name) return;
      post(API + '/kilns', { name: name, duplicate_from: source }).done(refresh).fail(showErrors);
    });
    $('#btn_kiln_rename').on('click', function () {
      var current = $('#kiln-select').val();
      if (!current) { $.bootstrapGrowl('Select a kiln to rename', { type: 'warning' }); return; }
      var name = window.prompt('New name for "' + current + '":', current);
      if (!name || name === current) return;
      $.ajax({
        url: API + '/kilns/' + encodeURIComponent(current), type: 'PUT',
        contentType: 'application/json', data: JSON.stringify({ name: name })
      }).done(refresh).fail(showErrors);
    });
    $('#btn_kiln_delete').on('click', function () {
      var name = $('#kiln-select').val();
      if (!name) { $.bootstrapGrowl('Select a kiln to delete', { type: 'warning' }); return; }
      if (!window.confirm('Delete kiln settings profile "' + name + '"? This cannot be undone.')) return;
      $.ajax({ url: API + '/kilns/' + encodeURIComponent(name), type: 'DELETE' })
        .done(refresh).fail(showErrors);
    });
    $('#btn_kiln_activate').on('click', function () {
      var name = $('#kiln-select').val() || null;
      post(API + '/active_kiln', { name: name }).done(function (resp) {
        if (resp.restart_required) {
          $.bootstrapGrowl('Kiln activated — a restart is required for some of its settings', { type: 'warning' });
        } else {
          $.bootstrapGrowl('Kiln activated', { type: 'success' });
        }
        refresh();
      }).fail(showErrors);
    });
  }

  function showErrors(xhr) {
    var errors = (xhr.responseJSON || {}).errors || { error: 'request failed' };
    Object.keys(errors).forEach(function (k) {
      $.bootstrapGrowl(k + ': ' + errors[k], { type: 'danger' });
    });
  }

  // --- save flow --------------------------------------------------------
  function saveScope(scope) {
    var values = dirty[scope];
    var reset = Object.keys(resets[scope]);
    var payload = { scope: scope, values: values, reset: reset };

    // Mid-firing ignore_* changes need a typed confirmation (ledger D114).
    if (snap.oven_state !== 'IDLE') {
      var needsConfirm = Object.keys(values).some(function (k) {
        return snap.schema[k].mid_firing_editable;
      }) || reset.some(function (k) {
        return snap.schema[k].mid_firing_editable;
      });
      if (needsConfirm) {
        var typed = window.prompt(
          'You are changing thermocouple error handling WHILE THE KILN IS FIRING.\n' +
          'Type IGNORE to confirm:');
        if (typed !== 'IGNORE') {
          $.bootstrapGrowl('Save cancelled', { type: 'warning' });
          return;
        }
        payload.confirm = true;
      }
    }

    post(API, payload).done(function (resp) {
      var outcomes = resp.outcomes || {};
      var counts = { 'applied': 0, 'applied-next-firing': 0, 'restart-required': 0 };
      Object.keys(outcomes).forEach(function (k) { counts[outcomes[k]]++; });
      if (counts['applied']) {
        $.bootstrapGrowl(counts['applied'] + ' setting(s) applied', { type: 'success' });
      }
      if (counts['applied-next-firing']) {
        $.bootstrapGrowl(counts['applied-next-firing'] + ' setting(s) saved — take effect at the next firing', { type: 'info' });
      }
      if (counts['restart-required']) {
        $.bootstrapGrowl(counts['restart-required'] + ' setting(s) saved — restart required', { type: 'warning' });
      }
      refresh(); // re-renders; banner driven by server-side restart_pending (D115)
    }).fail(function (xhr) {
      // Per-key errors: mark rows, keep edits so the user can fix them.
      var errors = (xhr.responseJSON || {}).errors || {};
      $('.setting-error').remove();
      Object.keys(errors).forEach(function (k) {
        var $row = $('.setting-row[data-key="' + k + '"]');
        if ($row.length) {
          $row.append($('<p class="setting-error">').text(errors[k]));
        } else {
          $.bootstrapGrowl(k + ': ' + errors[k], { type: 'danger' });
        }
      });
    });
  }

  // --- restart ------------------------------------------------------------
  function pollUntilBack(attempt) {
    if (attempt > 30) {
      $('#restart-status').text('Server did not come back — check the Pi.');
      return;
    }
    $.getJSON(API).done(function () {
      window.location.reload();
    }).fail(function () {
      setTimeout(function () { pollUntilBack(attempt + 1); }, 2000);
    });
  }

  function bindSaveAndRestart() {
    $('#btn_save_kiln').on('click', function () { saveScope('kiln'); });
    $('#btn_save_global').on('click', function () { saveScope('global'); });
    $('#btn_restart').on('click', function () {
      if (!window.confirm('Restart the kiln controller now? (Only possible while idle.)')) return;
      post(API + '/restart', {}).done(function () {
        $('#btn_restart').prop('disabled', true);
        $('#restart-status').text('Restarting…');
        setTimeout(function () { pollUntilBack(0); }, 2000);
      }).fail(function (xhr) {
        var err = ((xhr.responseJSON || {}).error) || 'restart refused';
        $.bootstrapGrowl(err, { type: 'danger' });
      });
    });
  }

  $(function () {
    bindKilnBar();
    bindSaveAndRestart();
    refresh();
    // Keep oven-state gating fresh, but never clobber in-progress edits.
    setInterval(function () { if (!hasDirty()) refresh(); }, 10000);
  });
})();
