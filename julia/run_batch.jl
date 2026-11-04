include("run_opf.jl")
using PowerModelsSecurityConstrained

function contingency_label(contingency, position)
    for key in (:contingency_id, :id, :label)
        haskey(contingency, key) && return String(contingency[key])
    end
    return "contingency_$(position)"
end

function add_scopf_contingencies!(pm_data, case_data, contingencies)
    branch_index = Dict(String(branch[:branch_id]) => idx for (idx, branch) in enumerate(case_data[:branches]))
    gen_index = Dict(String(gen[:gen_id]) => idx for (idx, gen) in enumerate(case_data[:generators]))
    branch_contingencies = []
    gen_contingencies = []

    for (position, contingency) in enumerate(contingencies)
        event_type = haskey(contingency, :event_type) ? String(contingency[:event_type]) : "simultaneous"
        event_type == "simultaneous" || throw(ArgumentError(
            "PMSC coupled SCOPF supports static N-1 contingencies; got event_type=$(event_type)",
        ))
        components = haskey(contingency, :components) ? contingency[:components] : []
        length(components) == 1 || throw(ArgumentError(
            "PMSC coupled SCOPF requires exactly one outaged component per contingency; got $(length(components))",
        ))

        component = only(components)
        component_type = String(component[:type])
        component_id = String(component[:id])
        label = contingency_label(contingency, position)
        if component_type == "branch"
            haskey(branch_index, component_id) || throw(ArgumentError("unknown branch contingency id: $(component_id)"))
            push!(branch_contingencies, (idx=branch_index[component_id], label=label, type="branch"))
        elseif component_type == "generator"
            haskey(gen_index, component_id) || throw(ArgumentError("unknown generator contingency id: $(component_id)"))
            push!(gen_contingencies, (idx=gen_index[component_id], label=label, type="gen"))
        else
            throw(ArgumentError("unsupported contingency component type: $(component_type)"))
        end
    end

    pm_data["branch_contingencies"] = branch_contingencies
    pm_data["gen_contingencies"] = gen_contingencies
    return pm_data
end

const PMSC = PowerModelsSecurityConstrained
const _PM = PowerModels
const SOLVED_STATUSES = ("LOCALLY_SOLVED", "OPTIMAL", "ALMOST_LOCALLY_SOLVED", "ALMOST_OPTIMAL")

# PMSC build_c1_scopf with optional removal of constraint groups, used to attribute infeasibility.
function build_scopf_relaxed(pm::_PM.AbstractPowerModel; link_vm=true, cont_thermal=true, cont_angle=true)
    _PM.variable_bus_voltage(pm, nw=0)
    _PM.variable_gen_power(pm, nw=0)
    _PM.variable_branch_power(pm, nw=0)
    _PM.constraint_model_voltage(pm, nw=0)
    for i in _PM.ids(pm, :ref_buses, nw=0)
        _PM.constraint_theta_ref(pm, i, nw=0)
    end
    for i in _PM.ids(pm, :bus, nw=0)
        _PM.constraint_power_balance(pm, i, nw=0)
    end
    for i in _PM.ids(pm, :branch, nw=0)
        _PM.constraint_ohms_yt_from(pm, i, nw=0)
        _PM.constraint_ohms_yt_to(pm, i, nw=0)
        _PM.constraint_voltage_angle_difference(pm, i, nw=0)
        _PM.constraint_thermal_limit_from(pm, i, nw=0)
        _PM.constraint_thermal_limit_to(pm, i, nw=0)
    end
    for nw in [id for id in _PM.nw_ids(pm) if id != 0]
        _PM.variable_bus_voltage(pm, nw=nw, bounded=false)
        _PM.variable_gen_power(pm, nw=nw, bounded=false)
        _PM.variable_branch_power(pm, nw=nw, bounded=cont_thermal)
        PMSC.variable_c1_response_delta(pm, nw=nw)
        _PM.constraint_model_voltage(pm, nw=nw)
        for i in _PM.ids(pm, :ref_buses, nw=nw)
            _PM.constraint_theta_ref(pm, i, nw=nw)
        end
        gen_buses = _PM.ref(pm, :gen_buses, nw=nw)
        for i in _PM.ids(pm, :bus, nw=nw)
            _PM.constraint_power_balance(pm, i, nw=nw)
            if link_vm && i in gen_buses
                PMSC.constraint_c1_voltage_magnitude_link(pm, i, nw_1=0, nw_2=nw)
            end
        end
        response_gens = _PM.ref(pm, :response_gens, nw=nw)
        for (i, gen) in _PM.ref(pm, :gen, nw=nw)
            if i in response_gens
                PMSC.constraint_c1_gen_power_real_response(pm, i, nw_1=0, nw_2=nw)
            else
                PMSC.constraint_c1_gen_power_real_link(pm, i, nw_1=0, nw_2=nw)
            end
        end
        for i in _PM.ids(pm, :branch, nw=nw)
            _PM.constraint_ohms_yt_from(pm, i, nw=nw)
            _PM.constraint_ohms_yt_to(pm, i, nw=nw)
            cont_angle && _PM.constraint_voltage_angle_difference(pm, i, nw=nw)
            if cont_thermal
                _PM.constraint_thermal_limit_from(pm, i, nw=nw)
                _PM.constraint_thermal_limit_to(pm, i, nw=nw)
            end
        end
    end
    PMSC.objective_c1_variable_pg_cost_basecase(pm)
    pg_cost = _PM.var(pm, 0, :pg_cost)
    _PM.JuMP.@objective(pm.model, Min, sum(pg_cost[i] for (i, gen) in _PM.ref(pm, 0, :gen)))
end

const SCREEN_RELAXATIONS = [
    ("no_post_contingency_thermal", (; cont_thermal=false)),
    ("no_generator_voltage_link", (; link_vm=false)),
    ("no_post_contingency_angle", (; cont_angle=false)),
    ("no_thermal_no_voltage_link", (; link_vm=false, cont_thermal=false)),
]

function overloaded_branches(mn, sol; top=10)
    rows = Any[]
    for (i, br) in get(sol, "branch", Dict())
        rate = mn["nw"]["1"]["branch"][i]["rate_a"]
        rate > 0 || continue
        s = max(hypot(br["pf"], br["qf"]), hypot(br["pt"], br["qt"]))
        s / rate > 1.0 + 1e-4 || continue
        push!(rows, Dict("branch_id" => mn["nw"]["1"]["branch"][i]["source_id"][2],
                         "loading_pct" => round(100 * s / rate; digits=1)))
    end
    sort!(rows; by=r -> -r["loading_pct"])
    return rows[1:min(top, end)]
end

function generator_voltage_shifts(mn, sol0, sol; top=10)
    rows = Any[]
    for bus in mn["nw"]["1"]["gen_buses"]
        k = string(bus)
        dv = sol["bus"][k]["vm"] - sol0["bus"][k]["vm"]
        abs(dv) > 1e-4 || continue
        push!(rows, Dict("bus_id" => k, "vm_base" => round(sol0["bus"][k]["vm"]; digits=4),
                         "vm_post" => round(sol["bus"][k]["vm"]; digits=4)))
    end
    sort!(rows; by=r -> -abs(r["vm_post"] - r["vm_base"]))
    return rows[1:min(top, end)]
end

const CLASS_EXPLANATIONS = Dict(
    "post_contingency_thermal_overload" =>
        "Outage cannot be secured with area-participation response: post-contingency flows exceed emergency ratings (rate_c); feasible once those limits are dropped.",
    "generator_voltage_setpoint_lock" =>
        "Outage cannot be secured while generator-bus voltages stay at base-case setpoints (PV hold); feasible once that link is dropped.",
    "thermal_or_voltage_lock" =>
        "Dropping either the emergency ratings or the generator voltage hold alone restores feasibility; both are individually binding.",
    "coupled_thermal_and_voltage_limits" =>
        "Feasible only when both emergency ratings and the generator voltage hold are dropped; no single constraint group explains it.",
    "post_contingency_angle_limit" =>
        "Feasible once post-contingency branch angle-difference limits are dropped.",
    "unresolved_infeasibility" =>
        "No tested relaxation restores feasibility; likely power-balance/reactive deficit or islanding in the post-contingency network.",
)

function classify_infeasibility(ok::Set{String})
    th = "no_post_contingency_thermal" in ok
    vm = "no_generator_voltage_link" in ok
    th && vm && return "thermal_or_voltage_lock"
    th && return "post_contingency_thermal_overload"
    vm && return "generator_voltage_setpoint_lock"
    "no_post_contingency_angle" in ok && return "post_contingency_angle_limit"
    "no_thermal_no_voltage_link" in ok && return "coupled_thermal_and_voltage_limits"
    return "unresolved_infeasibility"
end

function screen_contingency(pm_data, case_data, contingency, optimizer)
    data = deepcopy(pm_data)
    add_scopf_contingencies!(data, case_data, [contingency])
    mn = build_c1_scopf_multinetwork(data)
    attempt(name; kw...) = begin
        out = _PM.solve_model(mn, ACPPowerModel, optimizer, pm -> build_scopf_relaxed(pm; kw...); multinetwork=true)
        term = string(out["termination_status"])
        (out, Dict("variant" => name, "termination_status" => term,
                   "objective" => sanitize_json_value(get(out, "objective", nothing)),
                   "solve_time" => sanitize_json_value(get(out, "solve_time", nothing))))
    end
    row = Dict{String,Any}(
        "contingency_id" => contingency_label(contingency, 1),
        "components" => sanitize_json_value(contingency[:components]),
    )
    full_out, full_row = attempt("full")
    variants = Any[full_row]
    row["variants"] = variants
    if full_row["termination_status"] in SOLVED_STATUSES
        row["screening_outcome"] = "admitted_to_scopf"
        row["security_status"] = "securable_by_preventive_dispatch"
        row["infeasibility_class"] = nothing
        return row
    end
    solved = Set{String}()
    evidence = Dict{String,Any}()
    for (name, kw) in SCREEN_RELAXATIONS
        out, vrow = attempt(name; kw...)
        push!(variants, vrow)
        vrow["termination_status"] in SOLVED_STATUSES || continue
        push!(solved, name)
        sol = out["solution"]["nw"]
        if name == "no_post_contingency_thermal"
            evidence["overloaded_branches"] = overloaded_branches(mn, sol["1"])
        elseif name == "no_generator_voltage_link"
            evidence["generator_voltage_shifts"] = generator_voltage_shifts(mn, sol["0"], sol["1"])
        end
    end
    cls = classify_infeasibility(solved)
    row["screening_outcome"] = "excluded_from_scopf"
    row["security_status"] = "not_securable_by_preventive_dispatch"
    row["infeasibility_class"] = cls
    row["sufficient_relaxations"] = sort!(collect(solved))
    row["explanation"] = CLASS_EXPLANATIONS[cls]
    row["evidence"] = evidence
    return row
end

function solve_scopf_request(case_data, payload)
    result = Dict{String, Any}(
        "success" => false,
        "termination_status" => "not_implemented",
        "solver_name" => "powermodels_security_constrained",
        "task" => "scopf",
        "cost_convention" => "per_unit_scaled_v2",
    )

    try
        contingencies = haskey(payload, :contingencies) ? payload[:contingencies] : []
        isempty(contingencies) && throw(ArgumentError("at least one N-1 contingency is required"))
        pm_data = to_powermodels_data(case_data)
        options = haskey(payload, :options) ? payload[:options] : Dict()
        solver_attrs = Pair{String,Any}[
            "print_level" => 0,
            "sb" => "yes",
            "tol" => haskey(options, :tol) ? Float64(options[:tol]) : 1e-8,
        ]
        haskey(options, :max_iter) && push!(solver_attrs, "max_iter" => Int(options[:max_iter]))
        let linear_solver = String(strip(get(ENV, "IPOPT_LINEAR_SOLVER", "")))
            !isempty(linear_solver) && push!(solver_attrs, "linear_solver" => linear_solver)
        end
        let hsl_library = String(strip(get(ENV, "IPOPT_HSL_LIBRARY", "")))
            !isempty(hsl_library) && push!(solver_attrs, "hsllib" => hsl_library)
        end
        optimizer = optimizer_with_attributes(Ipopt.Optimizer, solver_attrs...)
        result["contingency_count"] = length(contingencies)
        admitted = collect(contingencies)
        if get(options, :screen_contingencies, false) === true
            screening = [screen_contingency(pm_data, case_data, c, optimizer) for c in contingencies]
            result["contingency_screening"] = screening
            admitted = [c for (c, s) in zip(contingencies, screening) if s["screening_outcome"] == "admitted_to_scopf"]
        end
        result["admitted_contingency_count"] = length(admitted)
        if isempty(admitted)
            result["termination_status"] = "no_admissible_contingencies"
            return result
        end
        add_scopf_contingencies!(pm_data, case_data, admitted)
        multinetwork = build_c1_scopf_multinetwork(pm_data)
        pm_out = run_c1_scopf(multinetwork, ACPPowerModel, optimizer)
        term = string(pm_out["termination_status"])
        result["success"] = term in SOLVED_STATUSES
        result["termination_status"] = term
        result["objective"] = sanitize_json_value(get(pm_out, "objective", nothing))
        result["solve_time"] = sanitize_json_value(get(pm_out, "solve_time", nothing))
        result["raw_result"] = sanitize_json_value(pm_out)
    catch err
        result["termination_status"] = err isa ArgumentError ? "invalid_input" : "exception"
        result["error"] = sprint(showerror, err)
        result["stacktrace"] = sprint(showerror, err, catch_backtrace())
    end
    return result
end

if abspath(PROGRAM_FILE) == @__FILE__
    if length(ARGS) < 3
        error("usage: run_batch.jl <case_json> <payload_json> <out_json>")
    end

    case_data = JSON3.read(read(ARGS[1], String))
    payload = JSON3.read(read(ARGS[2], String))
    result = solve_scopf_request(case_data, payload)
    open(ARGS[3], "w") do io
        JSON3.write(io, result)
    end
end
