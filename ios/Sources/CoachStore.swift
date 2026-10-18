import Foundation
import SwiftUI

@MainActor
final class CoachStore: ObservableObject {
    @Published var today: CoachDay?
    @Published var workouts: [PlannedWorkout] = []
    @Published var profile: AthleteProfile?
    @Published var error: String?
    @Published var busy = false
    @AppStorage("serverURL") var serverURL = ""

    private func request<T: Decodable>(_ path: String, method: String = "GET", body: Data? = nil) async throws -> T {
        guard let base = URL(string: serverURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(base.scheme ?? ""), base.host != nil,
              base.user == nil, base.password == nil, base.query == nil, base.fragment == nil,
              let url = URL(string: path, relativeTo: base)?.absoluteURL else {
            throw APIError.message("Inserisci l'indirizzo del server, ad esempio https://coach.local:8000")
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.httpBody = body
        req.timeoutInterval = 30
        if body != nil { req.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await URLSession.shared.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw APIError.message("Il server non ha completato la richiesta. Verifica connessione e configurazione.")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    func refresh() async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            let day: CoachDay = try await request("/coach/today")
            let rows: [PlannedWorkout] = try await request("/coach/workouts?limit=100")
            let athlete: AthleteProfile = try await request("/athlete/profile")
            today = day; workouts = rows; profile = athlete
        } catch { self.error = error.localizedDescription }
    }

    func saveGoal(_ goal: AthleteProfile.Goals) async {
        guard !busy else { return }
        busy = true; error = nil
        do {
            // Merge with the current complete profile: the legacy PUT replaces omitted fields.
            var full: [String: JSONValue] = try await request("/athlete/profile")
            full["goals"] = .object([
                "event": goal.event.map(JSONValue.string) ?? .null,
                "event_date": goal.event_date.map(JSONValue.string) ?? .null,
                "target_time": goal.target_time.map(JSONValue.string) ?? .null
            ])
            let saved: AthleteProfile = try await request("/athlete/profile", method: "PUT", body: JSONEncoder().encode(full))
            profile = saved
        } catch { self.error = error.localizedDescription }
        busy = false
        if error == nil { await refresh() }
    }

    func plan(_ sport: String) async {
        guard !busy else { return }
        busy = true; error = nil
        do {
            let _: SessionPlan = try await request("/coach/workouts", method: "POST", body: JSONEncoder().encode(["sport": sport]))
        } catch { self.error = error.localizedDescription }
        busy = false
        if error == nil { await refresh() }
    }
}

enum APIError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case let .message(text) = self { return text }; return nil }
}

// Preserve all legacy profile keys, including nested and unknown fields.
indirect enum JSONValue: Codable {
    case object([String: JSONValue]), array([JSONValue]), string(String), number(Double), bool(Bool), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([String: JSONValue].self) { self = .object(v) }
        else { self = .array(try c.decode([JSONValue].self)) }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .object(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .null: try c.encodeNil()
        }
    }
}
