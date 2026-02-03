from lib.oven import Profile, Segment
import os
import json
import pytest


def get_profile(file="test-fast.json"):
    profile_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'Test', file))
    print(profile_path)
    with open(profile_path) as infile:
        profile_json = json.dumps(json.load(infile))
    profile = Profile(profile_json)
    return profile


def get_v2_profile():
    """Create a test v2 profile"""
    return Profile(json.dumps({
        "name": "test-v2",
        "version": 2,
        "start_temp": 65,
        "temp_units": "f",
        "segments": [
            {"rate": 100, "target": 200, "hold": 0},
            {"rate": 50, "target": 250, "hold": 60},
            {"rate": 200, "target": 1000, "hold": 0}
        ]
    }))


# =============================================================================
# Test Segment Class
# =============================================================================

class TestSegment:
    def test_segment_creation(self):
        seg = Segment(100, 500, hold=30)
        assert seg.rate == 100
        assert seg.target == 500
        assert seg.hold == 1800  # 30 minutes in seconds
    
    def test_segment_default_hold(self):
        seg = Segment(100, 500)
        assert seg.hold == 0
    
    def test_is_ramp(self):
        ramp_pos = Segment(100, 500)
        ramp_neg = Segment(-50, 200)
        hold = Segment(0, 500, hold=60)
        max_seg = Segment("max", 1000)
        cool_seg = Segment("cool", 200)
        
        assert ramp_pos.is_ramp() == True
        assert ramp_neg.is_ramp() == True
        assert hold.is_ramp() == False
        assert max_seg.is_ramp() == False
        assert cool_seg.is_ramp() == False
    
    def test_is_pure_hold(self):
        hold = Segment(0, 500, hold=60)
        ramp = Segment(100, 500)
        
        assert hold.is_pure_hold() == True
        assert ramp.is_pure_hold() == False
    
    def test_has_hold_phase(self):
        with_hold = Segment(100, 500, hold=30)
        without_hold = Segment(100, 500, hold=0)
        pure_hold = Segment(0, 500, hold=60)
        
        assert with_hold.has_hold_phase() == True
        assert without_hold.has_hold_phase() == False
        assert pure_hold.has_hold_phase() == True
    
    def test_is_max_power(self):
        max_seg = Segment("max", 1000)
        normal_seg = Segment(100, 500)
        
        assert max_seg.is_max_power() == True
        assert normal_seg.is_max_power() == False
    
    def test_is_natural_cool(self):
        cool_seg = Segment("cool", 200)
        normal_seg = Segment(-50, 200)
        
        assert cool_seg.is_natural_cool() == True
        assert normal_seg.is_natural_cool() == False
    
    def test_validate_positive_rate_increasing_temp(self):
        seg = Segment(100, 500)
        # Should not raise
        seg.validate(previous_target=200)
    
    def test_validate_negative_rate_decreasing_temp(self):
        seg = Segment(-50, 200)
        # Should not raise
        seg.validate(previous_target=500)
    
    def test_validate_rejects_positive_rate_decreasing_temp(self):
        seg = Segment(100, 200)
        with pytest.raises(ValueError):
            seg.validate(previous_target=500)
    
    def test_validate_rejects_negative_rate_increasing_temp(self):
        seg = Segment(-50, 500)
        with pytest.raises(ValueError):
            seg.validate(previous_target=200)
    
    def test_validate_no_previous_target(self):
        seg = Segment(100, 500)
        # Should not raise when no previous target
        seg.validate(previous_target=None)
    
    def test_repr(self):
        seg = Segment(100, 500, hold=30)
        repr_str = repr(seg)
        assert "100" in repr_str
        assert "500" in repr_str
        assert "30" in repr_str


# =============================================================================
# Test Profile V2 Format
# =============================================================================

class TestProfileV2:
    def test_load_v2_profile(self):
        profile = get_v2_profile()
        assert profile.version == 2
        assert profile.start_temp == 65
        assert len(profile.segments) == 3
    
    def test_segment_access(self):
        profile = get_v2_profile()
        assert profile.segments[0].rate == 100
        assert profile.segments[0].target == 200
        assert profile.segments[1].hold == 3600  # 60 min in seconds
    
    def test_estimate_duration(self):
        profile = get_v2_profile()
        duration = profile.estimate_duration()
        # Expected: 
        # Seg 1: (200-65)/100 = 1.35 hours = 4860 seconds
        # Seg 2: (250-200)/50 = 1 hour = 3600 seconds + 60 min hold = 3600 seconds
        # Seg 3: (1000-250)/200 = 3.75 hours = 13500 seconds
        # Total: 4860 + 3600 + 3600 + 13500 = 25560 seconds
        assert 20000 < duration < 30000
    
    def test_to_legacy_format(self):
        profile = get_v2_profile()
        legacy = profile.to_legacy_format()
        
        assert legacy[0] == [0, 65]  # Start point
        assert len(legacy) >= 4  # At least start + 3 ramp endpoints + 1 hold endpoint
    
    def test_get_duration(self):
        profile = get_v2_profile()
        duration = profile.get_duration()
        assert duration > 0
    
    def test_get_target_temperature(self):
        profile = get_v2_profile()
        # At time 0, should be start temp
        temp = profile.get_target_temperature(0)
        assert temp == 65
    
    def test_get_segment_for_temperature_ramp(self):
        profile = get_v2_profile()
        idx, seg, phase = profile.get_segment_for_temperature(100, 0)
        assert idx == 0
        assert phase == 'ramp'
    
    def test_get_segment_for_temperature_hold(self):
        profile = get_v2_profile()
        # At segment target (within tolerance), should be in hold
        idx, seg, phase = profile.get_segment_for_temperature(198, 0)
        assert phase == 'hold'
    
    def test_get_rate_for_segment(self):
        profile = get_v2_profile()
        assert profile.get_rate_for_segment(0) == 100
        assert profile.get_rate_for_segment(1) == 50
        assert profile.get_rate_for_segment(2) == 200
    
    def test_get_hold_duration(self):
        profile = get_v2_profile()
        assert profile.get_hold_duration(0) == 0
        assert profile.get_hold_duration(1) == 3600  # 60 min in seconds
        assert profile.get_hold_duration(2) == 0


# =============================================================================
# Test Legacy Profile Loading
# =============================================================================

class TestProfileLegacy:
    def test_load_legacy_profile(self):
        profile = get_profile()
        # Should auto-convert to segments
        assert hasattr(profile, 'segments')
        assert len(profile.segments) > 0
    
    def test_legacy_start_temp(self):
        profile = get_profile()
        assert profile.start_temp == 200  # First data point temp
    
    def test_legacy_version(self):
        profile = get_profile()
        assert profile.version == 1
    
    def test_legacy_data_preserved(self):
        profile = get_profile()
        # Legacy data should still be accessible
        assert hasattr(profile, 'data')
        assert len(profile.data) > 0


# =============================================================================
# Test Original Profile Methods (Backward Compatibility)
# =============================================================================

class TestProfileBackwardCompatibility:
    def test_get_target_temperature(self):
        profile = get_profile()
        
        temperature = profile.get_target_temperature(3000)
        assert int(temperature) == 200
        
        temperature = profile.get_target_temperature(6004)
        assert temperature == 801.0
    
    def test_find_time_from_temperature(self):
        profile = get_profile()
        
        time = profile.find_next_time_from_temperature(500)
        assert time == 4800
        
        time = profile.find_next_time_from_temperature(2004)
        assert time == 10857.6
        
        time = profile.find_next_time_from_temperature(1900)
        assert time == 10400.0
    
    def test_find_time_odd_profile(self):
        profile = get_profile("test-cases.json")
        
        time = profile.find_next_time_from_temperature(500)
        assert time == 4200
        
        time = profile.find_next_time_from_temperature(2023)
        assert time == 16676.0
    
    def test_find_x_given_y_on_line_from_two_points(self):
        profile = get_profile()
        
        y = 500
        p1 = [3600, 200]
        p2 = [10800, 2000]
        time = profile.find_x_given_y_on_line_from_two_points(y, p1, p2)
        assert time == 4800
        
        # Flat segment - should return None (converted to 0 in old behavior)
        y = 500
        p1 = [3600, 200]
        p2 = [10800, 200]
        time = profile.find_x_given_y_on_line_from_two_points(y, p1, p2)
        assert time is None
        
        # Temperature above flat segment
        y = 500
        p1 = [3600, 600]
        p2 = [10800, 600]
        time = profile.find_x_given_y_on_line_from_two_points(y, p1, p2)
        assert time is None
        
        # Temperature at flat segment
        y = 500
        p1 = [3600, 500]
        p2 = [10800, 500]
        time = profile.find_x_given_y_on_line_from_two_points(y, p1, p2)
        assert time is None


# =============================================================================
# Test Edge Cases
# =============================================================================

class TestEdgeCases:
    def test_single_segment_profile(self):
        """Test profile with only one segment"""
        profile = Profile(json.dumps({
            "name": "test-single",
            "version": 2,
            "start_temp": 65,
            "segments": [
                {"rate": 100, "target": 500, "hold": 0}
            ]
        }))
        assert len(profile.segments) == 1
        assert profile.estimate_duration() > 0
    
    def test_all_max_rates(self):
        """Test profile with all max rate segments"""
        profile = Profile(json.dumps({
            "name": "test-max",
            "version": 2,
            "start_temp": 65,
            "segments": [
                {"rate": "max", "target": 500, "hold": 0},
                {"rate": "max", "target": 1000, "hold": 30}
            ]
        }))
        assert profile.segments[0].is_max_power()
        assert profile.segments[1].is_max_power()
        # Should still estimate duration
        duration = profile.estimate_duration()
        assert duration > 0
    
    def test_cool_segments(self):
        """Test profile with cooling segments"""
        profile = Profile(json.dumps({
            "name": "test-cool",
            "version": 2,
            "start_temp": 1000,
            "segments": [
                {"rate": "cool", "target": 500, "hold": 0},
                {"rate": -100, "target": 200, "hold": 0}
            ]
        }))
        assert profile.segments[0].is_natural_cool()
        assert profile.segments[1].rate == -100
    
    def test_valley_profile(self):
        """Test profile with temperature decreasing then increasing"""
        profile = Profile(json.dumps({
            "name": "test-valley",
            "version": 2,
            "start_temp": 500,
            "segments": [
                {"rate": -100, "target": 200, "hold": 30},
                {"rate": 200, "target": 800, "hold": 0}
            ]
        }))
        assert len(profile.segments) == 2
        legacy = profile.to_legacy_format()
        # Should have start, valley, hold, peak
        assert len(legacy) >= 4
    
    def test_zero_duration_hold(self):
        """Test segment with hold=0"""
        profile = Profile(json.dumps({
            "name": "test-no-hold",
            "version": 2,
            "start_temp": 65,
            "segments": [
                {"rate": 100, "target": 500, "hold": 0}
            ]
        }))
        assert profile.segments[0].hold == 0
        assert not profile.segments[0].has_hold_phase()
    
    def test_validation_rejects_invalid_rate_direction(self):
        """Test that invalid rate/direction combinations are rejected"""
        with pytest.raises(ValueError):
            Profile(json.dumps({
                "name": "test-invalid",
                "version": 2,
                "start_temp": 200,
                "segments": [
                    {"rate": -100, "target": 500, "hold": 0}  # Negative rate, higher target
                ]
            }))
    
    def test_pure_hold_segment(self):
        """Test profile with rate=0 (pure hold) segment"""
        profile = Profile(json.dumps({
            "name": "test-pure-hold",
            "version": 2,
            "start_temp": 500,
            "segments": [
                {"rate": 0, "target": 500, "hold": 60}
            ]
        }))
        idx, seg, phase = profile.get_segment_for_temperature(500, 0)
        assert phase == 'hold'
        assert seg.is_pure_hold()
    
    def test_legacy_hold_merge(self):
        """Test that consecutive holds in legacy format get merged"""
        profile = Profile(json.dumps({
            "name": "test-hold-merge",
            "data": [[0, 100], [3600, 200], [7200, 200], [10800, 200]],
            "type": "profile"
        }))
        # The two hold points should be merged into one segment
        # Segment 0: ramp from 100 to 200
        # Segment 1 hold should include both hold periods (7200 seconds total = 120 minutes)
        assert len(profile.segments) == 1  # Just the ramp, with hold attached
        assert profile.segments[0].target == 200
        assert profile.segments[0].hold == 7200  # 120 minutes in seconds
    
    def test_get_segment_for_temp_beyond_segments(self):
        """Test behavior when segment_index exceeds segment count"""
        profile = get_v2_profile()
        idx, seg, phase = profile.get_segment_for_temperature(1000, 10)
        assert phase == 'complete'
        assert idx == len(profile.segments) - 1
    
    def test_get_rate_for_segment_beyond_count(self):
        """Test get_rate_for_segment when index is out of bounds"""
        profile = get_v2_profile()
        assert profile.get_rate_for_segment(100) == 0
    
    def test_get_hold_duration_beyond_count(self):
        """Test get_hold_duration when index is out of bounds"""
        profile = get_v2_profile()
        assert profile.get_hold_duration(100) == 0
