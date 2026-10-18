import Foundation

struct CoachDay: Decodable {
    let reason: String
    let fitness: Fitness
    let readiness: Readiness
    let event: Event
    let workout: SessionPlan
    struct Fitness: Decodable { let ctl: Double; let atl: Double; let tsb: Double }
    struct Readiness: Decodable { let readiness: Double?; let confidence: Double }
    struct Event: Decodable { let name: String?; let days_to_event: Int? }
}

struct SessionPlan: Decodable {
    let name: String
    let sport: String
    let duration_min: Int
    let reason: String
    let steps: [Step]
    struct Step: Decodable {
        let type: String
        let duration_min: Int
        let target_power_w: Double?
    }
}

struct PlannedWorkout: Decodable, Identifiable {
    let id: Int
    let date: String
    let status: String
    let payload: SessionPlan
}

struct AthleteProfile: Codable {
    var goals: Goals
    struct Goals: Codable {
        var event: String?
        var event_date: String?
        var target_time: String?
    }
}

func sportName(_ value: String) -> String {
    ["cycling": "Bici", "running": "Corsa", "swimming": "Nuoto"][value] ?? value
}

func stepName(_ value: String) -> String {
    ["warmup": "Riscaldamento", "cooldown": "Defaticamento", "easy": "Facile",
     "endurance": "Resistenza", "interval": "Ripetuta", "recovery": "Recupero",
     "aerobic": "Aerobico", "tempo": "Ritmo sostenuto"][value] ?? value
}
