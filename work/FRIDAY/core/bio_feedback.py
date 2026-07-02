import time

class BioFeedbackEngine:
    def __init__(self):
        self.flow_metrics = {
            "typing_speed": 0,
            "error_rate": 0,
            "interaction_density": 0
        }
        self.current_state = "focused"

    def analyze_interaction(self, key_count, time_spent):
        speed = key_count / time_spent if time_spent > 0 else 0
        self.flow_metrics["typing_speed"] = speed
        
        if speed > 5 and speed < 10:
            self.current_state = "flow"
        elif speed > 10:
            self.current_state = "stressed"
        else:
            self.current_state = "relaxed"
            
        return self.current_state

    def get_system_adjustment(self):
        if self.current_state == "flow":
            return "Minimize interruptions. Focus mode engaged."
        elif self.current_state == "stressed":
            return "Recommend 5-minute break. Playing calming focus music."
        return "Standard operations maintained."
