#!/usr/bin/env python3
"""
Migrate legacy v1 profiles to v2 rate-based format.

Usage: 
    python migrate_profiles.py [--dry-run] [--backup]
    
Options:
    --dry-run   Show changes without writing
    --backup    Create backups before modifying
    --profile-dir PATH   Profile directory (default: storage/profiles)
"""

import json
import os
import sys
import argparse
import shutil
from datetime import datetime


def convert_v1_to_v2(profile):
    """Convert a v1 time-based profile to v2 rate-based format"""
    if profile.get("version", 1) >= 2:
        return profile  # Already v2
    
    data = profile.get("data", [])
    if len(data) < 2:
        return None  # Invalid profile
    
    segments = []
    start_temp = data[0][1]
    
    for i in range(1, len(data)):
        prev_time, prev_temp = data[i-1]
        curr_time, curr_temp = data[i]
        
        time_diff = curr_time - prev_time  # seconds
        temp_diff = curr_temp - prev_temp  # degrees
        
        if time_diff > 0:
            if temp_diff != 0:
                # Ramp segment
                rate = (temp_diff / time_diff) * 3600  # degrees per hour
                segments.append({
                    "rate": round(rate, 1),
                    "target": curr_temp,
                    "hold": 0
                })
            else:
                # Hold segment - merge with previous if possible
                hold_minutes = time_diff / 60
                if segments and segments[-1]["target"] == curr_temp:
                    segments[-1]["hold"] += round(hold_minutes, 1)
                else:
                    segments.append({
                        "rate": 0,
                        "target": curr_temp,
                        "hold": round(hold_minutes, 1)
                    })
    
    return {
        "name": profile["name"],
        "type": "profile",
        "version": 2,
        "start_temp": start_temp,
        "temp_units": profile.get("temp_units", "f"),
        "segments": segments,
        "_migrated_from_v1": True,
        "_original_data": data
    }


def main():
    parser = argparse.ArgumentParser(description='Migrate profiles to v2 format')
    parser.add_argument('--dry-run', action='store_true', 
                        help='Show changes without writing')
    parser.add_argument('--backup', action='store_true', 
                        help='Create backups before modifying')
    parser.add_argument('--profile-dir', default=None, 
                        help='Profile directory')
    args = parser.parse_args()
    
    # Determine profile directory
    if args.profile_dir:
        profile_dir = args.profile_dir
    else:
        # Default: storage/profiles relative to script location
        script_dir = os.path.dirname(os.path.abspath(__file__))
        profile_dir = os.path.join(script_dir, '..', 'storage', 'profiles')
        profile_dir = os.path.abspath(profile_dir)
    
    if not os.path.exists(profile_dir):
        print(f"Error: Profile directory not found: {profile_dir}")
        sys.exit(1)
    
    print(f"Profile directory: {profile_dir}")
    print(f"Dry run: {args.dry_run}")
    print(f"Backup: {args.backup}")
    print("-" * 60)
    
    # Create backup directory if needed
    if args.backup and not args.dry_run:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = os.path.join(os.path.dirname(profile_dir), f"profiles_backup_{timestamp}")
        shutil.copytree(profile_dir, backup_dir)
        print(f"Backup created: {backup_dir}")
        print("-" * 60)
    
    # Statistics
    stats = {
        "skipped_v2": 0,
        "converted": 0,
        "errors": 0,
        "total": 0
    }
    
    # Process each profile
    for filename in sorted(os.listdir(profile_dir)):
        if not filename.endswith('.json'):
            continue
        
        stats["total"] += 1
        filepath = os.path.join(profile_dir, filename)
        
        try:
            with open(filepath, 'r') as f:
                profile = json.load(f)
            
            if profile.get("version", 1) >= 2:
                print(f"SKIP (already v2): {filename}")
                stats["skipped_v2"] += 1
                continue
            
            converted = convert_v1_to_v2(profile)
            if converted is None:
                print(f"ERROR (invalid): {filename}")
                stats["errors"] += 1
                continue
            
            if args.dry_run:
                print(f"WOULD CONVERT: {filename}")
                print(f"  Start temp: {converted['start_temp']}")
                print(f"  Segments: {len(converted['segments'])}")
                for i, seg in enumerate(converted['segments']):
                    hold_str = f", hold={seg['hold']}min" if seg['hold'] > 0 else ""
                    print(f"    {i+1}: rate={seg['rate']}°/hr, target={seg['target']}{hold_str}")
                print()
                stats["converted"] += 1
            else:
                with open(filepath, 'w') as f:
                    json.dump(converted, f, indent=2)
                print(f"CONVERTED: {filename}")
                stats["converted"] += 1
        
        except json.JSONDecodeError as e:
            print(f"ERROR (JSON): {filename} - {e}")
            stats["errors"] += 1
        except Exception as e:
            print(f"ERROR: {filename} - {e}")
            stats["errors"] += 1
    
    # Print summary
    print("-" * 60)
    print("Summary:")
    print(f"  Total profiles: {stats['total']}")
    print(f"  Already v2 (skipped): {stats['skipped_v2']}")
    print(f"  Converted: {stats['converted']}")
    print(f"  Errors: {stats['errors']}")
    
    if args.dry_run and stats['converted'] > 0:
        print("\nThis was a dry run. Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
