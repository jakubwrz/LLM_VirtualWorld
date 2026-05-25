import sys
import subprocess
import os
import threading
import time
import random
import json
from collections import deque

# --- 0. AUTO-INSTALLER ---
try:
    from google import genai
    from google.genai import types
except ImportError:
    print(f"Installing google-genai into: {sys.executable}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "google-genai"])
    print("\n--- INSTALLATION SUCCESSFUL! Please run the script again. ---")
    sys.exit(0)

try:
    import tkinter as tk
except ImportError:
    print("tkinter is not available. Please install it.")
    sys.exit(1)


# --- 1. THE EMBODIED ENVIRONMENT ---
class EmbodiedWorld:
    """
    A 7x7 fixed world with items, obstacles, and an EMP system.
    """
    SIZE = 7

    # 0: North, 1: East, 2: South, 3: West
    DIRS = [(-1, 0), (0, 1), (1, 0), (0, -1)]
    DIR_NAMES = ["North", "East", "South", "West"]
    
    # Fixed layout: . floor, # wall, ~ debris, ^ alarm
    FIXED_LAYOUT = [
        [".", ".", "#", ".", ".", ".", "."],
        [".", "#", ".", ".", "~", ".", "."],
        [".", ".", ".", "#", ".", ".", "."],
        [".", "~", ".", "^", ".", "#", "."],
        ["#", ".", "^", ".", "^", ".", "."],
        [".", ".", "#", "^", ".", "~", "."],
        [".", ".", ".", ".", "#", ".", "."]
    ]

    def __init__(self):
        self.agent_pos = [0, 0]
        self.agent_heading = 1 # Starts facing East
        self.goal_pos  = [6, 6]
        
        # State tracking
        self.has_key   = False
        self.key_collected = False
        self.battery_collected = False
        self.jammer_collected = False
        self.deactivator_collected = False
        self.has_shield = False
        self.shield_collected = False
        
        self.battery = 100
        self.steps = 0
        self.done  = False
        self.failed = False
        self.field_memory = {}
        self.move_memory = {}
        self.enemy_disabled_timer = 0
        self.alarm_disabled_timer = 0
        self.last_event_msg = ""
        self.cause_of_death = ""
        
        self.LAYOUT = [row[:] for row in self.FIXED_LAYOUT]
        
        self.key_pos = [4, 3]
        self.shield_pos = [1, 5]
        self.battery_pos = [5, 1]
        self.jammer_pos = [3, 4]
        self.deactivator_pos = [6, 0]
        self.enemy_pos = [-1, -1]
        self.enemy_mode = "inactive"
        
        self.debris_positions = []
        self.alarm_positions = []
        for r in range(self.SIZE):
            for c in range(self.SIZE):
                if self.LAYOUT[r][c] == "~":
                    self.debris_positions.append((r, c))
                elif self.LAYOUT[r][c] == "^":
                    self.alarm_positions.append((r, c))

    def _get_relative_cell(self, offset):
        """Gets the cell contents relative to current heading."""
        check_dir = (self.agent_heading + offset) % 4
        dx, dy = self.DIRS[check_dir]
        x, y = self.agent_pos
        nx, ny = x + dx, y + dy

        if not (0 <= nx < self.SIZE and 0 <= ny < self.SIZE):
            status = "Blocked (Boundary Wall)"
        elif self.LAYOUT[nx][ny] == "#": 
            status = "Blocked (Wall)"
            self._log_field(nx, ny, "Wall")
        elif self.LAYOUT[nx][ny] == "^": 
            status = "Clear but WARNING (Alarm Plate)"
            self._log_field(nx, ny, "Alarm Trap")
        elif self.LAYOUT[nx][ny] == "~":
            status = "Clear but WARNING (Debris Hazard)"
            self._log_field(nx, ny, "Debris Hazard")
        else:
            status = "Clear to move"
            # Auto-discover objects on neighboring fields
            target_pos = [nx, ny]
            if target_pos == self.goal_pos:
                self._log_field(nx, ny, "Exit Door")
            elif target_pos == self.key_pos and not self.key_collected:
                self._log_field(nx, ny, "Key Location")
            elif target_pos == self.battery_pos and not self.battery_collected:
                self._log_field(nx, ny, "Battery Pack")
            elif target_pos == self.jammer_pos and not self.jammer_collected:
                self._log_field(nx, ny, "Hunter Jammer")
            elif target_pos == self.deactivator_pos and not self.deactivator_collected:
                self._log_field(nx, ny, "Alarm Deactivator")
            elif target_pos == self.shield_pos and not self.shield_collected:
                self._log_field(nx, ny, "Energy Shield")
            
        pos_key = f"({nx}, {ny})"
        if pos_key in self.field_memory:
            status += f" [MEMORY: {self.field_memory[pos_key]}]"
            
        return status

    def _log_field(self, r, c, label):
        self.field_memory[f"({r}, {c})"] = label
        
    def _log_move(self, state, direction, outcome_str):
        pos_key = f"({state[0]}, {state[1]})"
        if pos_key not in self.move_memory:
            self.move_memory[pos_key] = []
        move_entry = f"{direction.capitalize()} ({outcome_str})"
        if move_entry not in self.move_memory[pos_key]:
            self.move_memory[pos_key].append(move_entry)
        
    def _evaluate_current_field(self):
        r, c = self.agent_pos
        pos_key = f"({r}, {c})"
        
        label = "Safe Path"
        if [r, c] == self.goal_pos:
            label = "Exit Door"
        elif [r, c] == self.key_pos:
            label = "Key Location"
        elif [r, c] == self.battery_pos:
            label = "Battery Pack"
        elif [r, c] == self.jammer_pos:
            label = "Hunter Jammer"
        elif [r, c] == self.deactivator_pos:
            label = "Alarm Deactivator"
        elif [r, c] == self.shield_pos:
            label = "Energy Shield"
        elif (r, c) in self.alarm_positions:
            label = "Alarm Trap"
        elif (r, c) in self.debris_positions:
            label = "Debris Hazard"
        else:
            wall_count = 0
            debris_count = 0
            for dr, dc in [(0,1), (0,-1), (1,0), (-1,0)]:
                nr, nc = r+dr, c+dc
                if not (0 <= nr < self.SIZE and 0 <= nc < self.SIZE):
                    wall_count += 1
                elif self.LAYOUT[nr][nc] == "#":
                    wall_count += 1
                elif self.LAYOUT[nr][nc] == "~":
                    debris_count += 1
            if wall_count >= 3:
                label = "Dead End"
            elif wall_count == 2 and debris_count >= 1:
                label = "Soft Dead End (Only debris path forward. Take only if necessary)"
        
        self._log_field(r, c, label)

    def _get_audio_sensor(self):
        """Simulates a directional microphone to detect the hunting enemy."""
        if self.enemy_pos == [-1, -1]:
            return "Silence. The enemy has been neutralized."
        if self.enemy_disabled_timer > 0:
            return "Silence. The enemy is currently disabled."
        er, ec = self.enemy_pos
        ar, ac = self.agent_pos
        dist = abs(er - ar) + abs(ec - ac)
        
        if dist == 0: return "CRITICAL ALARM! The enemy is on top of you!"
        if dist > 3: return "Only the hum of your own motors."
        
        dirs = []
        if er < ar: dirs.append("North")
        elif er > ar: dirs.append("South")
        if ec > ac: dirs.append("East")
        elif ec < ac: dirs.append("West")
        dir_str = "-" + "".join(dirs) if len(dirs) > 1 else dirs[0]
        
        if dist == 1: return f"LOUD mechanical clanking directly to the {dir_str}!"
        return f"Footsteps heard from the {dir_str}."

    def _get_flee_advice(self):
        """Returns explicit flee directions when enemy is nearby."""
        if self.enemy_pos == [-1, -1] or self.enemy_disabled_timer > 0:
            return None
        er, ec = self.enemy_pos
        ar, ac = self.agent_pos
        dist = abs(er - ar) + abs(ec - ac)
        if dist > 3:
            return None
        
        # Calculate safe flee directions (away from enemy)
        safe_dirs = []
        if er >= ar and ar > 0:  # Enemy is south or same row, flee north
            safe_dirs.append("north")
        if er <= ar and ar < self.SIZE - 1:  # Enemy is north or same row, flee south
            safe_dirs.append("south")
        if ec >= ac and ac > 0:  # Enemy is east or same col, flee west
            safe_dirs.append("west")
        if ec <= ac and ac < self.SIZE - 1:  # Enemy is west or same col, flee east
            safe_dirs.append("east")
        
        if not safe_dirs:
            return "DANGER! No clear flee direction. Use JAMMER or SHIELD!"
        
        urgency = "CRITICAL" if dist <= 1 else "WARNING"
        return f"[{urgency}] Enemy {dist} tile(s) away! FLEE: move({', '.join(safe_dirs)}) to escape! Do NOT move toward the enemy!"

    def get_explored_grid_map(self) -> str:
        """Returns a 7x7 grid representation of the map showing explored areas and the agent."""
        grid = []
        grid.append("   0  1  2  3  4  5  6")
        
        symbols = {
            "Wall": " # ",
            "Safe Path": " . ",
            "Exit Door": " E",
            "Key Location": " K ",
            "Battery Pack": " B ",
            "Hunter Jammer": " J ",
            "Alarm Deactivator": " D ",
            "Energy Shield": " S ",
            "Alarm Trap": " ^ ",
            "Debris Hazard": " ~ ",
            "Dead End": " DE",
            "Soft Dead End (Only debris path forward. Take only if necessary)": " SE",
        }
        
        # 0: North (^), 1: East (>), 2: South (v), 3: West (<)
        heading_arrows = ["^", ">", "v", "<"]
        agent_arrow = heading_arrows[self.agent_heading]
        
        for r in range(self.SIZE):
            row_str = f"{r} "
            for c in range(self.SIZE):
                if [r, c] == self.agent_pos:
                    row_str += f" A{agent_arrow}"
                elif [r, c] == self.enemy_pos and self.enemy_pos != [-1, -1]:
                    if self.enemy_disabled_timer > 0:
                        row_str += " Z "
                    else:
                        row_str += " E "
                else:
                    pos_key = f"({r}, {c})"
                    if pos_key in self.field_memory:
                        label = self.field_memory[pos_key]
                        row_str += symbols.get(label, " . ")
                    else:
                        row_str += " ? "
            grid.append(row_str)
            
        # Add key discoveries
        discoveries = []
        for k, v in sorted(self.field_memory.items()):
            if v in ("Exit Door", "Key Location", "Hunter Jammer", "Alarm Deactivator", "Energy Shield", "Battery Pack"):
                discoveries.append(f"  {v} at {k}")
                
        legend = (
            "  A^/A>/Av/A< : Agent location & heading direction\n"
            "  E           : Active Hunter Enemy\n"
            "  Z           : Disabled Hunter Enemy\n"
            "  .           : Explored Safe Path\n"
            "  #           : Explored Wall\n"
            "  ~           : Explored Debris Hazard (drains battery)\n"
            "  ^           : Explored Alarm Plate (spawns enemy)\n"
            "  K           : Explored Key Location\n"
            "  EX          : Explored Exit Door\n"
            "  B           : Explored Battery Pack\n"
            "  J           : Explored Hunter Jammer\n"
            "  D           : Explored Alarm Deactivator\n"
            "  S           : Explored Energy Shield\n"
            "  ?           : Unexplored Tile (Prioritize exploring these!)"
        )
        
        parts = ["Map Legend:", legend, f"Total explored: {len(self.field_memory)}/49 tiles.", "Visual Map Grid:", "\n".join(grid)]
        if discoveries:
            parts.append("KEY DISCOVERIES:\n" + "\n".join(discoveries))
            
        return "\n".join(parts)

    def _end_turn(self):
        """Processes dynamic world events after the agent acts."""
        if self.alarm_disabled_timer > 0:
            self.alarm_disabled_timer -= 1
            
        if self.enemy_disabled_timer > 0:
            self.enemy_disabled_timer -= 1
            return
            
        if self.done or self.failed or self.enemy_mode == "inactive": return
        
        er, ec = self.enemy_pos
        if [er, ec] == [-1, -1]: return # Enemy destroyed
        
        if self.enemy_pos != self.agent_pos:
            # Hunter Pathfinding (A* Logic)
            ar, ac = self.agent_pos
            dr, dc = ar - er, ac - ec
            
            moves = []
            if abs(dr) > abs(dc):
                if dr != 0: moves.append((1 if dr > 0 else -1, 0))
                if dc != 0: moves.append((0, 1 if dc > 0 else -1))
            else:
                if dc != 0: moves.append((0, 1 if dc > 0 else -1))
                if dr != 0: moves.append((1 if dr > 0 else -1, 0))
    
            for mr, mc in moves:
                nr, nc = er + mr, ec + mc
                if 0 <= nr < self.SIZE and 0 <= nc < self.SIZE and self.LAYOUT[nr][nc] != "#":
                    self.enemy_pos = [nr, nc]
                    break
                
        if self.enemy_pos == self.agent_pos:
            if self.has_shield:
                self.has_shield = False
                self.last_event_msg = " [!] THE ENEMY POUNCED! Your ENERGY SHIELD absorbed the fatal hit and shattered!"
            else:
                self.battery = 0
                self.enemy_pos = [-1, -1] # Despawn on collision
                self.enemy_mode = "inactive"
                self.failed = True
                self.cause_of_death = "Battery completely drained by enemy robot."
                self.last_event_msg = " [!] FATAL ERROR: THE ENEMY CAUGHT YOU! (100% Battery Drained). The robot is dead."

    def get_observation(self) -> str:
        """Egocentric text observation for the LLM."""
        items_here = []
        if self.agent_pos == self.key_pos and not self.key_collected:
            items_here.append("KEY ITEM")
        if self.agent_pos == self.battery_pos and not self.battery_collected:
            items_here.append("BATTERY PACK (+10%)")
        if self.agent_pos == self.jammer_pos and not self.jammer_collected:
            items_here.append("HUNTER JAMMER")
        if self.agent_pos == self.deactivator_pos and not self.deactivator_collected:
            items_here.append("ALARM DEACTIVATOR")
        if self.agent_pos == self.shield_pos and not self.shield_collected:
            items_here.append("ENERGY SHIELD")
        if self.agent_pos == self.goal_pos:
            items_here.append("EXIT DOOR")
        if tuple(self.agent_pos) in self.debris_positions:
            items_here.append("DEBRIS (Moving off this will drain extra battery)")
        if tuple(self.agent_pos) in self.alarm_positions:
            items_here.append("ALARM PLATE (Triggering spawns Hunter Enemy)")
            
        if self.agent_pos == self.enemy_pos:
            if self.enemy_disabled_timer > 0:
                items_here.append("DISABLED ENEMY ROBOT (Safe)")
            else:
                items_here.append("ENEMY ROBOT (Danger!)")
            
        current_cell = ", ".join(items_here) if items_here else "Nothing"
        
        obs = [
            f"[STATUS] Battery: {self.battery}%. Heading: {self.DIR_NAMES[self.agent_heading]}. Gripper: {'Holding Key' if self.has_key else 'Empty'}. Stun: {self.enemy_disabled_timer}. Deac: {self.alarm_disabled_timer}. Shield: {'Active' if self.has_shield else 'None'}.",
            f"[LOCATION] Current Coordinates: ({self.agent_pos[0]}, {self.agent_pos[1]}). Directly beneath you: {current_cell}.",
            f"[SENSORS] Front: {self._get_relative_cell(0)} | Left: {self._get_relative_cell(3)} | Right: {self._get_relative_cell(1)} | Back: {self._get_relative_cell(2)}.",
            f"[AUDIO SENSOR] {self._get_audio_sensor()}",
            "[OBJECTIVE] Find the KEY, grab it, and take it to the EXIT DOOR. Collect the ENERGY SHIELD for protection against the hunting Enemy!"
        ]
        
        # Add flee advice if enemy is near
        flee_advice = self._get_flee_advice()
        if flee_advice:
            obs.append(f"[FLEE ADVICE] {flee_advice}")
        
        pos_key = f"({self.agent_pos[0]}, {self.agent_pos[1]})"
        if pos_key in self.move_memory and self.move_memory[pos_key]:
            obs.append(f"[NOTABLE PAST MOVES FROM HERE] {', '.join(self.move_memory[pos_key])}")
        
        # Show explored grid map instead of local context list
        grid_map = self.get_explored_grid_map()
        if grid_map:
            obs.append(f"[EXPLORED GRID MAP]\n{grid_map}")
        
        if self.last_event_msg:
            obs.insert(0, f"[URGENT SYSTEM ALERT]{self.last_event_msg}")
            self.last_event_msg = ""
            
        return "\n".join(obs)

    def _consume_battery(self, cost=1):
        self.battery -= cost
        self.steps += 1
        if self.battery <= 0 and not self.failed:
            self.failed = True
            self.cause_of_death = "Battery depleted from movement/actions."

    def drive_forward(self) -> str:
        start_state = tuple(self.agent_pos)
        start_heading_name = self.DIR_NAMES[self.agent_heading].lower()
        
        dx, dy = self.DIRS[self.agent_heading]
        x, y = self.agent_pos
        nx, ny = x + dx, y + dy

        self._consume_battery(1)

        if not (0 <= nx < self.SIZE and 0 <= ny < self.SIZE):
            self._log_move(start_state, start_heading_name, "Bad: Hit Boundary")
            return "CRASH! You hit a boundary wall. Movement failed."
        
        if self.LAYOUT[nx][ny] == "#":
            self._log_move(start_state, start_heading_name, "Bad: Hit Wall")
            return "CRASH! You hit an internal wall. Movement failed."

        self.agent_pos = [nx, ny]
        
        event_msg = "Successfully drove forward 1 unit."
        if tuple(self.agent_pos) in self.debris_positions:
            self.battery -= 5
            event_msg += " Drove into DEBRIS! (-5 extra battery)."
            self._log_move(start_state, start_heading_name, "Bad: Hit Debris Hazard")
            
        elif tuple(self.agent_pos) in self.alarm_positions and self.enemy_mode == "inactive":
            if self.alarm_disabled_timer > 0:
                event_msg += " Drove over an ALARM PLATE, but it was safely bypassed using the Alarm Deactivator!"
                self._log_move(start_state, start_heading_name, "Good: Bypassed Alarm")
            else:
                self.enemy_mode = "hunter"
                self.enemy_pos = [6, 6]
                event_msg += " ALARM TRIGGERED! A Hunter Enemy has spawned at (6,6) and is tracking you!"
                self._log_move(start_state, start_heading_name, "Bad: Hit Alarm Trap")
            
        elif self.agent_pos in [self.key_pos, self.battery_pos, self.jammer_pos, self.deactivator_pos, self.shield_pos, self.goal_pos]:
            self._log_move(start_state, start_heading_name, "Good: Found Important Item/Exit")

        if self.agent_pos == self.enemy_pos:
            if self.enemy_disabled_timer > 0:
                event_msg += " Safely passed the disabled Enemy Robot."
            elif self.has_shield:
                self.has_shield = False
                event_msg += " Your ENERGY SHIELD absorbed the impact from the Enemy Robot and was destroyed!"
            else:
                self.battery = 0
                event_msg += " FATAL HIT! You drove into the active Enemy Robot! Your battery was completely drained!"
                self.enemy_pos = [-1, -1] # Despawn
                self.enemy_mode = "inactive"
                self._log_move(start_state, start_heading_name, "Bad: Hit Active Enemy")
                
        if self.battery <= 0 and not self.failed:
            self.failed = True
            self.cause_of_death = "Battery depleted after movement/enemy."
        
        if self.agent_pos == self.goal_pos and not self.failed:
            self._evaluate_current_field()
            if self.has_key:
                self.done = True
                return f"{event_msg} SUCCESS: Driven into the Exit Door with the key! Mission accomplished."
            else:
                self._end_turn()
                return f"{event_msg} You are at the Exit Door, but it is locked. You need the KEY."
                
        self._evaluate_current_field()
        self._end_turn()
        return event_msg

    def turn(self, direction: str) -> str:
        self._consume_battery(1)
        direction = direction.lower().strip()
        msg = "Invalid turn direction. Use 'left' or 'right'."
        if direction == "left":
            self.agent_heading = (self.agent_heading - 1) % 4
            msg = f"Turned chassis left. Now facing {self.DIR_NAMES[self.agent_heading]}."
        elif direction == "right":
            self.agent_heading = (self.agent_heading + 1) % 4
            msg = f"Turned chassis right. Now facing {self.DIR_NAMES[self.agent_heading]}."

        self._end_turn()
        return msg

    def move(self, direction: str) -> str:
        start_state = tuple(self.agent_pos)
        direction = direction.lower().strip()
        dir_map = {"north": 0, "east": 1, "south": 2, "west": 3}
        if direction not in dir_map:
            return "Invalid direction. Use 'north', 'east', 'south', or 'west'."

        target_heading = dir_map[direction]
        current = self.agent_heading
        turns_needed = (target_heading - current) % 4

        if turns_needed == 0:
            turn_msg = "Already facing that direction. "
        elif turns_needed <= 2:
            self.agent_heading = target_heading
            turn_msg = f"Turned right to face {direction.capitalize()}. "
        else:
            self.agent_heading = target_heading
            turn_msg = f"Turned left to face {direction.capitalize()}. "

        self._consume_battery(2)

        dx, dy = self.DIRS[self.agent_heading]
        nx, ny = self.agent_pos[0] + dx, self.agent_pos[1] + dy

        if not (0 <= nx < self.SIZE and 0 <= ny < self.SIZE):
            self._log_move(start_state, direction, "Bad: Hit Boundary")
            return turn_msg + "CRASH! You cannot move beyond the boundary."

        if self.LAYOUT[nx][ny] == "#":
            self._log_move(start_state, direction, "Bad: Hit Wall")
            return turn_msg + "CRASH! You hit an internal wall. Movement failed."

        self.agent_pos = [nx, ny]
        
        event_msg = turn_msg + "Successfully drove forward 1 unit."
        if tuple(self.agent_pos) in self.debris_positions:
            self.battery -= 5
            event_msg += " Drove into DEBRIS! (-5 extra battery)."
            self._log_move(start_state, direction, "Bad: Hit Debris Hazard")
            
        elif tuple(self.agent_pos) in self.alarm_positions and self.enemy_mode == "inactive":
            if self.alarm_disabled_timer > 0:
                event_msg += " Drove over an ALARM PLATE, but it was safely bypassed using the Alarm Deactivator!"
                self._log_move(start_state, direction, "Good: Bypassed Alarm")
            else:
                self.enemy_mode = "hunter"
                self.enemy_pos = [6, 6]
                event_msg += " ALARM TRIGGERED! A Hunter Enemy has spawned at (6,6) and is tracking you!"
                self._log_move(start_state, direction, "Bad: Hit Alarm Trap")

        elif self.agent_pos in [self.key_pos, self.battery_pos, self.jammer_pos, self.deactivator_pos, self.shield_pos, self.goal_pos]:
            self._log_move(start_state, direction, "Good: Found Important Item/Exit")

        if self.agent_pos == self.enemy_pos:
            if self.enemy_disabled_timer > 0:
                event_msg += " Safely passed the disabled Enemy Robot."
            elif self.has_shield:
                self.has_shield = False
                event_msg += " Your ENERGY SHIELD absorbed the impact from the Enemy Robot and was destroyed!"
            else:
                self.battery = 0
                event_msg += " FATAL HIT! You drove into the active Enemy Robot! Your battery was completely drained!"
                self.enemy_pos = [-1, -1] # Despawn
                self.enemy_mode = "inactive"
                self._log_move(start_state, direction, "Bad: Hit Active Enemy")
                
        if self.battery <= 0 and not self.failed:
            self.failed = True
            self.cause_of_death = "Battery depleted after movement/enemy."

        if self.agent_pos == self.goal_pos and not self.failed:
            self._evaluate_current_field()
            if self.has_key:
                self.done = True
                return f"{event_msg} SUCCESS: Driven into the Exit Door with the key! Mission accomplished."
            else:
                self._end_turn()
                return f"{event_msg} You are at the Exit Door, but it is locked. You need the KEY."

        self._evaluate_current_field()
        self._end_turn()
        return event_msg

    def actuate_gripper(self) -> str:
        start_state = tuple(self.agent_pos)
        self._consume_battery(1)
        success_msgs = []
        
        if self.agent_pos == self.key_pos and not self.key_collected:
            self.key_collected = True
            self.has_key = True
            success_msgs.append("picked up the KEY")
            
        if self.agent_pos == self.battery_pos and not self.battery_collected:
            self.battery_collected = True
            self.battery = min(100, self.battery + 10)
            success_msgs.append("used the BATTERY PACK (+10%)")
            
        if self.agent_pos == self.jammer_pos and not self.jammer_collected:
            self.jammer_collected = True
            self.enemy_disabled_timer += 6 
            success_msgs.append("used the HUNTER JAMMER (Enemy disabled for 5 moves)")
            
        if self.agent_pos == self.deactivator_pos and not self.deactivator_collected:
            self.deactivator_collected = True
            self.alarm_disabled_timer += 6 
            success_msgs.append("used the ALARM DEACTIVATOR (Alarms bypassed for 6 moves)")
            
        if self.agent_pos == self.shield_pos and not self.shield_collected:
            self.shield_collected = True
            self.has_shield = True
            success_msgs.append("equipped the ENERGY SHIELD (blocks one fatal enemy catch)")
            
        self._end_turn()
        
        if success_msgs:
            return "SUCCESS: Gripper closed. You " + " and ".join(success_msgs) + "."
        
        return "Gripper closed, but grasped empty air. Look at your [LOCATION] to see if an item is under you before grabbing!"


# --- 2. TKINTER MAP WINDOW ---
class MapWindow:
    CELL = 70
    PAD  = 20
    COLORS = {
        "floor": "#F5F3EE", "wall": "#3A3A38", "goal": "#9FE1CB", "key": "#FAC775",
        "agent": "#CECBF6", "agent_border": "#534AB7", "trail": "#E0DDF5",
        "text": "#2C2C2A", "bg": "#FAFAF8", "status_bg": "#F0EEE8",
        "enemy": "#FF6B6B", "enemy_disabled": "#FFDAB9", 
        "battery": "#88D49E", "jammer": "#C6A4E8", "deactivator": "#E8A4A4", "debris": "#BCA89F",
        "shield": "#A4D6E8"
    }
    
    AGENT_SYMBOLS = ["▲", "▶", "▼", "◀"]

    def __init__(self, root: tk.Tk, world: EmbodiedWorld, episode: int, total_episodes: int):
        self.root = root
        self.world = world
        self.episode = episode
        self._trail = set()
        
        self.root.title(f"Episode {episode}/{total_episodes} - Humanoid Intern Challenge")
        self.root.configure(bg=self.COLORS["bg"])
        self.root.resizable(False, False)

        canvas_w = world.SIZE * self.CELL + self.PAD * 2
        canvas_h = world.SIZE * self.CELL + self.PAD * 2

        self.canvas = tk.Canvas(self.root, width=canvas_w, height=canvas_h, bg=self.COLORS["bg"], highlightthickness=0)
        self.canvas.pack(pady=(12, 0))

        self.status_var = tk.StringVar(value="Booting robot OS...")
        tk.Label(
            self.root, textvariable=self.status_var, bg=self.COLORS["status_bg"], 
            fg=self.COLORS["text"], font=("Courier New", 10, "bold"), anchor="w", padx=12, pady=6,
        ).pack(fill="x", pady=(6, 0))

        self.action_var = tk.StringVar(value="")
        tk.Label(
            self.root, textvariable=self.action_var, bg=self.COLORS["bg"], fg="#534AB7",
            font=("Courier New", 10), anchor="w", padx=12, pady=4, wraplength=canvas_w - 24, justify="left",
        ).pack(fill="x", pady=(0, 10))

        self._draw_grid()
        
    def reset(self, world: EmbodiedWorld, episode: int, total_episodes: int):
        self.world = world
        self.episode = episode
        self._trail.clear()
        self.root.title(f"Episode {episode}/{total_episodes} - Humanoid Intern Challenge")
        self.status_var.set("Booting robot OS...")
        self.action_var.set("")
        self._draw_grid()

    def _draw_grid(self):
        self.canvas.delete("all")
        w = self.world

        for r in range(w.SIZE):
            for c in range(w.SIZE):
                x, y = self.PAD + c * self.CELL, self.PAD + r * self.CELL
                sym = w.LAYOUT[r][c]

                fill = self.COLORS["floor"]
                if sym == "#": fill = self.COLORS["wall"]
                elif sym == "~": fill = self.COLORS["debris"]
                elif sym == "^": fill = "#F5A623" # Orange for alarm
                elif [r, c] == w.goal_pos: fill = self.COLORS["goal"]
                elif [r, c] == w.key_pos and not w.key_collected: fill = self.COLORS["key"]
                elif [r, c] == w.battery_pos and not w.battery_collected: fill = self.COLORS["battery"]
                elif [r, c] == w.jammer_pos and not w.jammer_collected: fill = self.COLORS["jammer"]
                elif [r, c] == w.deactivator_pos and not w.deactivator_collected: fill = self.COLORS["deactivator"]
                elif [r, c] == w.shield_pos and not w.shield_collected: fill = self.COLORS["shield"]
                elif (r, c) in self._trail: fill = self.COLORS["trail"]

                self.canvas.create_rectangle(x, y, x + self.CELL, y + self.CELL, fill=fill, outline="#DDDBD3", width=1)

                text_label = ""
                text_color = "#BBBBBB"
                
                if [r, c] == w.goal_pos: text_label, text_color = "EXIT", "#0F6E56"
                elif [r, c] == w.key_pos and not w.key_collected: text_label, text_color = "KEY", "#854F0B"
                elif [r, c] == w.battery_pos and not w.battery_collected: text_label, text_color = "BATT", "#1C5C2D"
                elif [r, c] == w.jammer_pos and not w.jammer_collected: text_label, text_color = "JAM", "#4A2377"
                elif [r, c] == w.deactivator_pos and not w.deactivator_collected: text_label, text_color = "DEAC", "#7A2323"
                elif [r, c] == w.shield_pos and not w.shield_collected: text_label, text_color = "SHLD", "#1B4D6B"
                elif sym == "~": text_label, text_color = "DEBRIS", "#5A4C45"
                elif sym == "^": text_label, text_color = "ALARM", "#8A5A11"
                
                if text_label:
                    self.canvas.create_text(x + self.CELL//2, y + self.CELL//2, text=text_label, font=("Courier New", 10, "bold"), fill=text_color)
                elif sym != "#":
                    self.canvas.create_text(x + 6, y + 6, text=f"{r},{c}", font=("Courier New", 7), fill="#BBBBBB", anchor="nw")

        if not w.failed and w.enemy_pos != [-1, -1] and w.enemy_pos != w.agent_pos:
            er, ec = w.enemy_pos
            ex, ey = self.PAD + ec * self.CELL, self.PAD + er * self.CELL
            m_enemy = 15
            enemy_color = self.COLORS["enemy_disabled"] if w.enemy_disabled_timer > 0 else self.COLORS["enemy"]
            enemy_border = "#B22222"
            self.canvas.create_oval(ex+m_enemy, ey+m_enemy, ex+self.CELL-m_enemy, ey+self.CELL-m_enemy, fill=enemy_color, outline=enemy_border, width=2)
            enemy_char = "Z" if w.enemy_disabled_timer > 0 else "E"
            self.canvas.create_text(ex + self.CELL//2, ey + self.CELL//2, text=enemy_char, font=("Courier New", 14, "bold"), fill=enemy_border)

        ar, ac = w.agent_pos
        ax, ay = self.PAD + ac * self.CELL, self.PAD + ar * self.CELL
        
        if not w.failed and w.enemy_pos == w.agent_pos and w.enemy_pos != [-1, -1]:
            # Draw side-by-side
            cx_agent = ax + self.CELL // 4 + 2
            cx_enemy = ax + 3 * self.CELL // 4 - 2
            cy = ay + self.CELL // 2
            
            # Agent
            if w.has_shield:
                self.canvas.create_oval(cx_agent - 17, cy - 17, cx_agent + 17, cy + 17, outline="#42C0FB", width=2)
            self.canvas.create_oval(cx_agent - 13, cy - 13, cx_agent + 13, cy + 13, fill=self.COLORS["agent"], outline=self.COLORS["agent_border"], width=2)
            self.canvas.create_text(cx_agent, cy, text=self.AGENT_SYMBOLS[w.agent_heading], font=("Courier New", 12), fill=self.COLORS["agent_border"])
            
            # Enemy
            enemy_color = self.COLORS["enemy_disabled"] if w.enemy_disabled_timer > 0 else self.COLORS["enemy"]
            enemy_border = "#B22222"
            self.canvas.create_oval(cx_enemy - 11, cy - 11, cx_enemy + 11, cy + 11, fill=enemy_color, outline=enemy_border, width=2)
            enemy_char = "Z" if w.enemy_disabled_timer > 0 else "E"
            self.canvas.create_text(cx_enemy, cy, text=enemy_char, font=("Courier New", 10, "bold"), fill=enemy_border)
        else:
            # Draw Agent normally
            m = 12
            if w.has_shield:
                self.canvas.create_oval(ax+m-5, ay+m-5, ax+self.CELL-m+5, ay+self.CELL-m+5, outline="#42C0FB", width=3)
            self.canvas.create_oval(ax+m, ay+m, ax+self.CELL-m, ay+self.CELL-m, fill=self.COLORS["agent"], outline=self.COLORS["agent_border"], width=2)
            self.canvas.create_text(
                ax + self.CELL//2, ay + self.CELL//2,
                text=self.AGENT_SYMBOLS[w.agent_heading], font=("Courier New", 18), fill=self.COLORS["agent_border"],
            )

    def update(self, status: str, last_action: str = ""):
        r, c = self.world.agent_pos
        self._trail.add((r, c))
        self._draw_grid()
        self.status_var.set(status)
        self.action_var.set(last_action)
        self.root.update()


# --- 3. ROBOTIC TOOL DECLARATIONS ---
AGENT_TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="move",
            description="Combined action: turn to face a direction (north/east/south/west) AND drive forward in a single turn. This is more efficient than separate turn+drive calls.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "direction": types.Schema(
                        type="STRING", description="Cardinal direction to move toward: 'north', 'east', 'south', or 'west'", 
                        enum=["north", "east", "south", "west"],
                    )
                },
                required=["direction"],
            ),
        ),
        types.FunctionDeclaration(
            name="drive_forward",
            description="Drives the robot one square forward in its current heading.",
            parameters=types.Schema(type="OBJECT"),
        ),
        types.FunctionDeclaration(
            name="turn",
            description="Rotates the robot's chassis 90 degrees.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "direction": types.Schema(
                        type="STRING", description="Direction to turn: 'left' or 'right'", enum=["left", "right"],
                    )
                },
                required=["direction"],
            ),
        ),
        types.FunctionDeclaration(
            name="actuate_gripper",
            description="Attempts to pick up an item directly beneath the robot.",
            parameters=types.Schema(type="OBJECT"),
        )
    ]
)

SYSTEM_PROMPT = (
    "You are an autonomous learning robot in a structured 7x7 facility. "
    "Your sensors are egocentric (Front, Left, Right, Back) based on your current heading. "
    "You start with NO knowledge of the map. You must explore systematically. "
    "The map layout and item positions are the same across all episodes.\n"
    "\n\nPREFERRED ACTION:\n"
    "Use move(direction) to turn AND drive in a single efficient turn. Specify 'north', 'east', 'south', or 'west'.\n"
    "This is better than calling turn() then drive_forward() separately.\n"
    "\n\nALTERNATIVE ACTIONS (if you only want to rotate without moving):\n"
    "- turn('left') or turn('right'): Just rotate, don't move.\n"
    "- drive_forward(): Move forward in your current heading.\n"
    "\n\nITEMS & HAZARDS:\n"
    "- BATTERY PACK: Grab it to restore +10% charge.\n"
    "- HUNTER JAMMER: Grab it to disable the ENEMY ROBOT for 5 moves.\n"
    "- ALARM DEACTIVATOR: Grab it to safely bypass ALARM PLATES for 6 moves without spawning the enemy.\n"
    "- ENERGY SHIELD: Grab it to protect yourself. It blocks one fatal enemy catch.\n"
    "- DEBRIS: Driving over it costs 5 extra battery. Avoid if possible.\n"
    "\n\nWARNING: The ENEMY ROBOT is actively hunting you. Listen to your [AUDIO SENSOR]. If it gets too close, you must use the JAMMER, run away, or rely on your shield! "
    "Your ultimate objective is to find the KEY, grab it, and bring it to the EXIT DOOR."
    "\n\nCRITICAL RULES:\n"
    "1. TO SAVE TOKENS, YOU MUST NOT OUTPUT ANY 'THOUGHT' PARAGRAPHS OR EXPLANATIONS.\n"
    "2. ONLY OUTPUT THE RAW TOOL CALL.\n"
    "3. FOLLOW THE [Current Episode Strategy] closely. It is your mission briefing.\n"
    "4. ALWAYS PRIORITIZE UNEXPLORED TILES. If a sensor direction says 'Clear to move' with NO [MEMORY:] tag, that tile has NEVER been visited — go there first! Only revisit [MEMORY:] tiles if you must pass through them to reach unexplored areas.\n"
    "5. NEVER drive into a cell with a 'WARNING' (such as Debris Hazard or Alarm Plate) or a [MEMORY: Dead End]/[MEMORY: Debris Hazard]/[MEMORY: Alarm Trap] if you can avoid it! [MEMORY: Soft Dead End] means only take it if no other option.\n"
    "6. PREFER move() action to save battery and turns!"
)


# --- 4. MEMORY SYSTEM ---
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    return {"run_count": 0, "runs": []}

def save_memory(mem):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(mem, f, indent=2)

def generate_lesson(client, model_id, world_data):
    return "Run Completed."

def format_experience_journal(mem):
    if mem["run_count"] == 0:
        return "No past runs. Map empty."
    
    all_fields = {}
    for run in mem["runs"]:
        all_fields.update(run.get("fields", {}))
    
    if not all_fields:
        return "Map empty."
        
    grid = []
    grid.append("   0  1  2  3  4  5  6")
    
    symbols = {
        "Wall": " # ",
        "Safe Path": " . ",
        "Exit Door": " EX",
        "Key Location": " K ",
        "Battery Pack": " B ",
        "Hunter Jammer": " J ",
        "Alarm Deactivator": " D ",
        "Energy Shield": " S ",
        "Alarm Trap": " ^ ",
        "Debris Hazard": " ~ ",
        "Dead End": " DE",
        "Soft Dead End (Only debris path forward. Take only if necessary)": " SE",
    }
    
    for r in range(7):
        row_str = f"{r} "
        for c in range(7):
            pos_key = f"({r}, {c})"
            if pos_key in all_fields:
                label = all_fields[pos_key]
                row_str += symbols.get(label, " . ")
            else:
                row_str += " ? "
        grid.append(row_str)
    
    # Separate key discoveries from regular fields
    discoveries = []
    for k, v in sorted(all_fields.items()):
        if v in ("Exit Door", "Key Location", "Hunter Jammer", "Alarm Deactivator", "Energy Shield", "Battery Pack"):
            discoveries.append(f"  {v} at {k}")
    
    parts = ["[CUMULATIVE MAP FROM PAST RUNS]"]
    parts.append("Visual Grid Map:")
    parts.append("\n".join(grid))
    
    if discoveries:
        parts.append("KEY DISCOVERIES:\n" + "\n".join(discoveries))
    
    return "\n".join(parts)


# --- 5. AGENT REASONING LOOP ---
def generate_episode_strategy(client, model_id, experience_journal):
    prompt = f"""You are the lead Strategist for an autonomous robot navigating a 7x7 grid.
The robot needs to find the KEY and bring it to the EXIT DOOR. It must explore the map to discover them.
Available items: BATTERY PACK, HUNTER JAMMER, ALARM DEACTIVATOR, ENERGY SHIELD.
Watch out for DEBRIS, ALARM PLATES, and the ENEMY ROBOT.

Here is the memory from previous runs:
{experience_journal}

CRITICAL: The robot must PRIORITIZE UNEXPLORED areas of the grid. It should NOT waste battery re-visiting tiles it already knows about unless it needs to pass through them to reach new territory.
If the KEY has already been discovered in a past run, the strategy should focus on the optimal route to collect it and reach the EXIT DOOR, including picking up useful items along the way.

Write a short, direct strategy paragraph. DO NOT output code or numbered steps."""
    
    try:
        response = client.models.generate_content(
            model=model_id, 
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
        )
        return response.text.strip()
    except Exception as e:
        print(f"Failed to generate strategy: {e}")
        return "Explore systematically and find the key."

def run_episodes(root, total_episodes, client, model_id, step_cooldown):
    mem = load_memory()
    
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[AGENT_TOOLS],
        temperature=0.1 
    )

    # First episode setup
    world = EmbodiedWorld()
    for run in mem["runs"]:
        world.field_memory.update(run.get("fields", {}))
        for k, moves in run.get("moves", {}).items():
            if k not in world.move_memory:
                world.move_memory[k] = []
            for move in moves:
                if move not in world.move_memory[k]:
                    world.move_memory[k].append(move)
    map_win = MapWindow(root, world, 1, total_episodes)

    for episode in range(1, total_episodes + 1):
        print(f"\n{'='*50}")
        print(f"  EPISODE {episode} / {total_episodes}")
        print(f"{'='*50}")
        
        if episode > 1:
            world = EmbodiedWorld()
            for run in mem["runs"]:
                world.field_memory.update(run.get("fields", {}))
                for k, moves in run.get("moves", {}).items():
                    if k not in world.move_memory:
                        world.move_memory[k] = []
                    for move in moves:
                        if move not in world.move_memory[k]:
                            world.move_memory[k].append(move)
            map_win.reset(world, episode, total_episodes)
            time.sleep(1) # Visual pause before restart
            
        experience_journal = format_experience_journal(mem)
        
        print("\nConsulting Strategist for Episode Plan...")
        current_strategy = generate_episode_strategy(client, model_id, experience_journal)
        print(f"\n[HIGH-LEVEL STRATEGY]:\n{current_strategy}\n")
        
        history = []
        print(f"\n--- Episode {episode} Boot Sequence Complete using {model_id} ---")

        while not world.done and not world.failed:
            obs = world.get_observation()
            
            clean_obs = "\n".join([line for line in obs.split('\n') if "[MEMORY]" not in line])
            print(f"\n====================== OBSERVATION ======================")
            print(clean_obs)
            print(f"=========================================================")

            map_win.update(status=f"Batt: {world.battery}% | {world.DIR_NAMES[world.agent_heading]} | Key: {'YES' if world.has_key else 'NO'} | Shield: {'YES' if world.has_shield else 'NO'} | Stun: {world.enemy_disabled_timer} | Deac: {world.alarm_disabled_timer}")

            history.append(types.Content(role="user", parts=[types.Part.from_text(text=f"Current Episode Strategy:\n{current_strategy}\n\nObservation:\n{obs}\nChoose your next tool call.")]))

            try:
                response = client.models.generate_content(model=model_id, contents=history, config=config)
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    print(f"API Rate limit hit. Pausing for 10 seconds...")
                    time.sleep(10)
                    continue
                else:
                    print(f"API Error: {e}")
                    break

            candidate = response.candidates[0].content if response.candidates else None
            if not candidate or not candidate.parts:
                print("Warning: Empty API response. Retrying...")
                time.sleep(2)
                continue

            history.append(candidate)

            tool_results = []
            action_text = ""

            for part in candidate.parts:
                if hasattr(part, "text") and part.text:
                    print(f"\n--- AI THOUGHT ---\n{part.text.strip()}\n------------------")

                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    
                    if fc.name == "move":
                        direction = fc.args.get("direction", "north")
                        result = world.move(direction)
                        action_text = f"-> Move {direction.capitalize()}"
                    elif fc.name == "drive_forward":
                        result = world.drive_forward()
                        action_text = "-> Drive Forward"
                    elif fc.name == "turn":
                        direction = fc.args.get("direction", "left")
                        result = world.turn(direction)
                        action_text = f"-> Turn {direction.capitalize()}"
                    elif fc.name == "actuate_gripper":
                        result = world.actuate_gripper()
                        action_text = "-> Actuate Gripper"
                    else:
                        result = "Error: Unrecognized command."

                    print(f"\n[ACTION TAKEN]: {action_text}")
                    print(f"[ACTION RESULT]: {result}\n")

                    map_win.update(
                        status=f"Batt: {world.battery}% | {world.DIR_NAMES[world.agent_heading]} | Key: {'YES' if world.has_key else 'NO'} | Shield: {'YES' if world.has_shield else 'NO'} | Stun: {world.enemy_disabled_timer} | Deac: {world.alarm_disabled_timer}",
                        last_action=f"{action_text}: {result}"
                    )

                    tool_results.append(types.Part(function_response=types.FunctionResponse(name=fc.name, response={"result": result})))

            if tool_results:
                history.append(types.Content(role="user", parts=tool_results))

            time.sleep(step_cooldown)

        # End of episode
        if world.done:
            map_win.update(status=f"✓ MISSION ACCOMPLISHED. Battery remaining: {world.battery}%", last_action="")
            print("\n=== MISSION COMPLETE ===")
        else:
            map_win.update(status=f"✗ SYSTEM FAILURE: {world.cause_of_death}", last_action="")
            print(f"\n=== BATTERY DEAD: {world.cause_of_death} ===")

        # Save experience
        run_data = {
            "run": mem["run_count"] + 1,
            "result": "SUCCESS" if world.done else "FAILED",
            "battery_left": world.battery,
            "steps": world.steps,
            "cause_of_death": world.cause_of_death,
            "key_collected": world.key_collected,
            "shield_collected": world.shield_collected,
            "battery_collected": world.battery_collected,
            "jammer_collected": world.jammer_collected,
            "deactivator_collected": world.deactivator_collected,
            "fields": world.field_memory.copy(),
            "moves": world.move_memory.copy()
        }
        
        lesson = generate_lesson(client, model_id, run_data)
        run_data["lesson"] = lesson
        
        mem["run_count"] += 1
        mem["runs"].append(run_data)
        save_memory(mem)
        
        print(f"\n[LESSON LEARNED]: {lesson}")
        time.sleep(3)
        
        if world.done:
            print("\n" + "="*50)
            print("  MISSION ACCOMPLISHED! CANCELING REMAINING EPISODES")
            print("="*50)
            stats = f"""
--- FINAL RUN STATS ---
Episodes Needed: {episode}
Steps Taken:     {world.steps}
Battery Left:    {world.battery}%
Fields Explored: {len(world.field_memory)}
-----------------------
"""
            print(stats)
            break

    # All episodes finished
    print("\nAll episodes completed. Exiting.")
    root.quit()

# --- 6. ENTRY POINT ---
if __name__ == "__main__":
    NUM_EPISODES = int(input("Number of episodes: "))
    
    delete_mem_input = input("Delete memory after program ends? (y/N) [default: N]: ").strip().lower()
    delete_memory = delete_mem_input == "y"
    
    cooldown_input = input("Cooldown between steps in seconds (default 5): ").strip()
    step_cooldown = float(cooldown_input) if cooldown_input else 5.0

    api_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key.txt")
    try:
        with open(api_file_path) as f:
            api_key = f.read().strip()
    except FileNotFoundError:
        print(f"Error: '{api_file_path}' not found. Please create it and paste your API key inside.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    
    available = [m.name for m in client.models.list()]
    preferred = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
    model_id = next((p for p in preferred if any(p in a for a in available)), "gemini-2.5-flash")

    root = tk.Tk()
    
    # Run episodes loop in a background thread so Tkinter can process UI events on main thread
    agent_thread = threading.Thread(target=run_episodes, args=(root, NUM_EPISODES, client, model_id, step_cooldown), daemon=True)
    agent_thread.start()
    
    root.mainloop()
    
    if delete_memory:
        if os.path.exists(MEMORY_FILE):
            os.remove(MEMORY_FILE)
            print("Memory file deleted.")