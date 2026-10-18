import SwiftUI

@main
struct IronmanCoachApp: App {
    @StateObject private var store = CoachStore()
    var body: some Scene {
        WindowGroup { RootView().environmentObject(store).tint(.teal) }
    }
}

struct RootView: View {
    @EnvironmentObject var store: CoachStore
    var body: some View {
        TabView {
            NavigationStack { dashboard }.tabItem { Label("Oggi", systemImage: "heart.text.square") }
            NavigationStack { workouts }.tabItem { Label("Allenamenti", systemImage: "figure.triathlon") }
            NavigationStack { GoalView() }.tabItem { Label("Obiettivo", systemImage: "flag.checkered") }
            NavigationStack { connections }.tabItem { Label("Connessioni", systemImage: "link") }
        }
        .safeAreaInset(edge: .top) {
            if store.busy { ProgressView().padding(6) }
            if let error = store.error {
                Text(error).font(.caption).foregroundStyle(.red).padding().frame(maxWidth: .infinity).background(.regularMaterial)
            }
        }
        .task { if !store.serverURL.isEmpty { await store.refresh() } }
    }

    private var dashboard: some View {
        List {
            if let day = store.today {
                Section("Verso la gara") {
                    Text(day.event.name ?? "Imposta il tuo obiettivo").font(.title2.bold())
                    if let days = day.event.days_to_event { Text(days >= 0 ? "Mancano \(days) giorni" : "Gara conclusa") }
                }
                Section("Condizione · stime del motore") {
                    LabeledContent("Fitness (CTL)", value: String(format: "%.1f", day.fitness.ctl))
                    LabeledContent("Fatica (ATL)", value: String(format: "%.1f", day.fitness.atl))
                    LabeledContent("Bilancio (TSB)", value: String(format: "%.1f", day.fitness.tsb))
                    LabeledContent("Recupero", value: day.readiness.readiness.map { String(format: "%.0f/100", $0) } ?? "Dati insufficienti")
                    LabeledContent("Confidenza recupero", value: String(format: "%.0f%%", day.readiness.confidence))
                }
                Section("Il consiglio di oggi") {
                    Text(day.reason)
                    NavigationLink { WorkoutDetail(plan: day.workout) } label: {
                        Label("\(sportName(day.workout.sport)) · \(day.workout.duration_min) min", systemImage: "play.circle")
                    }
                }
            } else {
                ContentUnavailableView("Inizia il tuo percorso", systemImage: "figure.triathlon", description: Text("Configura il backend in Connessioni e carica i dati. Non vengono mostrati valori simulati."))
            }
            Button("Aggiorna dati") { Task { await store.refresh() } }.disabled(store.busy)
        }
        .navigationTitle("Ironman Coach")
        .refreshable { await store.refresh() }
    }

    private var workouts: some View {
        List {
            Section("Prepara una seduta per oggi") {
                ForEach(["swimming", "cycling", "running"], id: \.self) { sport in
                    Button(sportName(sport)) { Task { await store.plan(sport) } }
                        .disabled(store.busy || store.today == nil)
                }
            }
            Section("Sedute salvate") {
                if store.workouts.isEmpty { Text("Nessuna seduta salvata").foregroundStyle(.secondary) }
                ForEach(store.workouts) { row in
                    NavigationLink { WorkoutDetail(plan: row.payload) } label: {
                        VStack(alignment: .leading) {
                            Text("\(sportName(row.payload.sport)) · \(row.payload.duration_min) min")
                            Text("\(row.date) · \(row.status)").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }.navigationTitle("Allenamenti")
    }

    private var connections: some View {
        Form {
            Section("Server personale") {
                TextField("https://coach.local:8000", text: $store.serverURL)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                Button("Collega e carica") { Task { await store.refresh() } }.disabled(store.busy)
                Text("Usa una rete privata. Il backend attuale è personale e non dispone di autenticazione per l'accesso pubblico.").font(.caption)
            }
            Section("Servizi sportivi") {
                LabeledContent("Garmin Connect", value: "Da integrare")
                LabeledContent("Strava", value: "Da integrare")
                LabeledContent("Apple Salute", value: "Da integrare")
                Text("Questa versione legge i dati già presenti nel backend. I collegamenti diretti richiedono implementazione e autorizzazioni dei rispettivi servizi.").font(.caption)
            }
        }.navigationTitle("Connessioni")
    }
}

struct WorkoutDetail: View {
    let plan: SessionPlan
    var body: some View {
        List {
            Section {
                Text(plan.name).font(.headline)
                Text("\(sportName(plan.sport)) · \(plan.duration_min) minuti")
                Text(plan.reason)
            }
            Section("Guida alla seduta") {
                ForEach(Array(plan.steps.enumerated()), id: \.offset) { index, step in
                    VStack(alignment: .leading, spacing: 6) {
                        Text("\(index + 1). \(stepName(step.type))").font(.headline)
                        Text("\(step.duration_min) minuti")
                        if let power = step.target_power_w { Text("Obiettivo: \(Int(power)) W").foregroundStyle(.secondary) }
                    }.padding(.vertical, 4)
                }
            }
        }.navigationTitle("La tua seduta")
    }
}

struct GoalView: View {
    @EnvironmentObject var store: CoachStore
    @State private var event = ""
    @State private var date = Date()
    @State private var target = ""
    private static var formatter: DateFormatter {
        let f = DateFormatter(); f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd"; return f
    }
    var body: some View {
        Form {
            Section("La tua prossima gara") {
                TextField("Nome gara · Ironman / 70.3 / triathlon", text: $event)
                DatePicker("Data", selection: $date, in: Date()..., displayedComponents: .date)
                TextField("Tempo obiettivo (facoltativo)", text: $target)
                Text("La data influenza la fase di preparazione. Distanza e tempo obiettivo non personalizzano ancora il motore esistente.").font(.caption)
                Button("Salva obiettivo") {
                    Task { await store.saveGoal(.init(event: event, event_date: Self.formatter.string(from: date), target_time: target.isEmpty ? nil : target)) }
                }.disabled(store.busy || store.profile == nil || event.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .navigationTitle("Obiettivo triathlon")
        .onAppear { load() }
        .onChange(of: store.profile?.goals.event_date) { _, _ in load() }
    }
    private func load() {
        guard let goal = store.profile?.goals else { return }
        event = goal.event ?? ""; target = goal.target_time ?? ""
        date = goal.event_date.flatMap { Self.formatter.date(from: $0) } ?? Date()
    }
}
