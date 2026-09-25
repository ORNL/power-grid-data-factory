using JSON3

include("run_opf.jl")

function apply_pf_controls!(pm_data, raw_controls)
    controls = sanitize_json_value(raw_controls)
    gen_controls = get(controls, "generators", Dict{String, Any}())
    known_generators = Set{String}()
    for gen in values(pm_data["gen"])
        gen_id = String(gen["source_id"][3])
        push!(known_generators, gen_id)
        if haskey(gen_controls, gen_id)
            control = gen_controls[gen_id]
            gen["pg"] = Float64(control["pg"]) / pm_data["baseMVA"]
            haskey(control, "qg") && control["qg"] !== nothing && (gen["qg"] = Float64(control["qg"]) / pm_data["baseMVA"])
            gen["vg"] = Float64(control["vg"])
            pm_data["bus"][string(gen["bus_idx"])]["vm"] = gen["vg"]
        end
    end
    unknown = sort(collect(setdiff(Set(keys(gen_controls)), known_generators)))
    isempty(unknown) || error("Unknown generator control IDs: $(join(unknown, ", "))")

    taps = get(controls, "transformer_taps", Dict{String, Any}())
    shifts = get(controls, "transformer_shifts", Dict{String, Any}())
    for branch in values(pm_data["branch"])
        branch_id = String(branch["source_id"][2])
        haskey(taps, branch_id) && (branch["tap"] = Float64(taps[branch_id]) == 0.0 ? 1.0 : Float64(taps[branch_id]))
        haskey(shifts, branch_id) && (branch["shift"] = deg2rad(Float64(shifts[branch_id])))
    end
    shunts = get(controls, "shunts", Dict{String, Any}())
    known_shunts = Set{String}()
    for shunt in values(pm_data["shunt"])
        shunt_id = "bus:$(shunt["source_id"][2])"
        push!(known_shunts, shunt_id)
        haskey(shunts, shunt_id) && (shunt["bs"] = Float64(shunts[shunt_id]) / pm_data["baseMVA"])
    end
    unknown_shunts = sort(collect(setdiff(Set(keys(shunts)), known_shunts)))
    isempty(unknown_shunts) || error("Unknown shunt control IDs: $(join(unknown_shunts, ", "))")
    return controls
end

function apply_payload_contingency!(pm_data, raw_contingency)
    contingency = sanitize_json_value(raw_contingency)
    event_type = get(contingency, "event_type", "simultaneous")
    components = if event_type == "sequential_n1n1"
        Any[get(contingency, "first_outage", Dict()), get(contingency, "second_outage", Dict())]
    elseif event_type == "sequential_cascade"
        get(contingency, "stages", Any[])
    else
        get(contingency, "components", Any[])
    end
    for component in components
        component_type = String(get(component, "type", ""))
        component_id = String(get(component, "id", ""))
        collection = component_type == "generator" ? "gen" : component_type == "branch" ? "branch" : ""
        isempty(collection) && continue
        source_position = component_type == "generator" ? 3 : 2
        for item in values(pm_data[collection])
            if String(item["source_id"][source_position]) == component_id
                item[component_type == "generator" ? "gen_status" : "br_status"] = 0
            end
        end
    end
end

function build_pf_with_pq_limits(pm::AbstractPowerModel)
    variable_bus_voltage(pm, bounded = false)
    variable_gen_power(pm, bounded = false)
    variable_dcline_power(pm, bounded = false)
    for i in ids(pm, :branch)
        expression_branch_power_ohms_yt_from(pm, i)
        expression_branch_power_ohms_yt_to(pm, i)
    end
    constraint_model_voltage(pm)
    for (i, bus) in ref(pm, :ref_buses)
        constraint_theta_ref(pm, i)
        constraint_voltage_magnitude_setpoint(pm, i)
        if length(ref(pm, :bus_gens, i)) > 1
            for generator_id in collect(ref(pm, :bus_gens, i))[2:end]
                constraint_gen_setpoint_active(pm, generator_id)
                constraint_gen_setpoint_reactive(pm, generator_id)
            end
        end
    end
    for (i, bus) in ref(pm, :bus)
        constraint_power_balance(pm, i)
        generators = collect(ref(pm, :bus_gens, i))
        if !isempty(generators) && !(i in ids(pm, :ref_buses))
            if bus["bus_type"] == 2
                constraint_voltage_magnitude_setpoint(pm, i)
                for generator_id in generators
                    constraint_gen_setpoint_active(pm, generator_id)
                end
            else
                for generator_id in generators
                    constraint_gen_setpoint_active(pm, generator_id)
                    constraint_gen_setpoint_reactive(pm, generator_id)
                end
            end
        end
    end
end

function enforce_reactive_limits!(pm_data, solution; tolerance=1e-8)
    converted = Int[]
    for (bus_key, bus) in pm_data["bus"]
        bus["bus_type"] in (2, 3) || continue
        generators = [gen for gen in values(pm_data["gen"]) if gen["gen_status"] != 0 && gen["gen_bus"] == bus["bus_i"]]
        isempty(generators) && continue
        solved_q = sum(Float64(solution["gen"][string(gen["index"])]["qg"]) for gen in generators)
        qmin = sum(Float64(gen["qmin"]) for gen in generators)
        qmax = sum(Float64(gen["qmax"]) for gen in generators)
        target_q = clamp(solved_q, qmin, qmax)
        if abs(target_q - solved_q) > tolerance
            if bus["bus_type"] == 3
                replacement = nothing
                for candidate in values(pm_data["bus"])
                    candidate_generators = [
                        gen for gen in values(pm_data["gen"])
                        if gen["gen_status"] != 0 && gen["gen_bus"] == candidate["bus_i"]
                    ]
                    if candidate["bus_type"] == 2 && !isempty(candidate_generators)
                        replacement = candidate
                        break
                    end
                end
                replacement === nothing && continue
                replacement["bus_type"] = 3
            end
            total_range = sum(max(0.0, Float64(gen["qmax"]) - Float64(gen["qmin"])) for gen in generators)
            for gen in generators
                fraction = total_range > 0.0 ? max(0.0, Float64(gen["qmax"]) - Float64(gen["qmin"])) / total_range : 1.0 / length(generators)
                gen["qg"] = Float64(gen["qmin"]) + fraction * (target_q - qmin)
            end
            bus["bus_type"] = 1
            push!(converted, Int(bus["bus_i"]))
        end
    end
    return converted
end

function normalize_reactive_dispatch!(pm_data, solution)
    for bus in values(pm_data["bus"])
        generators = [gen for gen in values(pm_data["gen"]) if gen["gen_status"] != 0 && gen["gen_bus"] == bus["bus_i"]]
        length(generators) > 1 || continue
        total_q = sum(Float64(solution["gen"][string(gen["index"])]["qg"]) for gen in generators)
        qmin = sum(Float64(gen["qmin"]) for gen in generators)
        qmax = sum(Float64(gen["qmax"]) for gen in generators)
        if qmin <= total_q <= qmax
            total_range = sum(max(0.0, Float64(gen["qmax"]) - Float64(gen["qmin"])) for gen in generators)
            for gen in generators
                fraction = total_range > 0.0 ? max(0.0, Float64(gen["qmax"]) - Float64(gen["qmin"])) / total_range : 1.0 / length(generators)
                solution["gen"][string(gen["index"])]["qg"] = Float64(gen["qmin"]) + fraction * (total_q - qmin)
            end
        end
    end
end

function solve_pf_with_controls(pm_data, optimizer, enforce_q_limits, q_limit_tolerance)
    converted_buses = Int[]
    pm_out = solve_pf(pm_data, ACPPowerModel, optimizer)
    if enforce_q_limits
        for _ in 1:length(pm_data["bus"])
            term = string(pm_out["termination_status"])
            term in ("LOCALLY_SOLVED", "OPTIMAL", "ALMOST_LOCALLY_SOLVED", "ALMOST_OPTIMAL") || break
            newly_converted = enforce_reactive_limits!(pm_data, pm_out["solution"]; tolerance=q_limit_tolerance)
            isempty(newly_converted) && break
            append!(converted_buses, newly_converted)
            pm_out = solve_model(pm_data, ACPPowerModel, optimizer, build_pf_with_pq_limits)
        end
    end
    if haskey(pm_out, "solution")
        normalize_reactive_dispatch!(pm_data, pm_out["solution"])
        update_data!(pm_data, pm_out["solution"])
        flows = calc_branch_flow_ac(pm_data)
        pm_out["solution"]["branch"] = flows["branch"]
    end
    return pm_out, unique(converted_buses)
end

function solve_pf_request(case_data, payload)
    result = Dict{String, Any}(
        "success" => false,
        "termination_status" => "not_solved",
        "solver_name" => "powermodels",
        "task" => "pf",
    )

    try
        pm_data = to_powermodels_data(case_data)
        controls = haskey(payload, :controls) ? apply_pf_controls!(pm_data, payload[:controls]) : Dict{String, Any}()
        haskey(payload, :contingency) && apply_payload_contingency!(pm_data, payload[:contingency])
        options = haskey(payload, :options) ? payload[:options] : Dict{String, Any}()
        tolerance = haskey(options, :tol) ? Float64(options[:tol]) : 1e-8
        enforce_q_limits = haskey(options, :enforce_q_limits) ? Bool(options[:enforce_q_limits]) : true
        q_limit_tolerance = haskey(options, :q_limit_tolerance) ? Float64(options[:q_limit_tolerance]) : 1e-8
        solver_attrs = Pair{String,Any}[
            "print_level" => 0,
            "sb" => "yes",
            "tol" => tolerance,
        ]
        let linear_solver = strip(get(ENV, "IPOPT_LINEAR_SOLVER", ""))
            !isempty(linear_solver) && push!(solver_attrs, "linear_solver" => linear_solver)
        end
        optimizer = optimizer_with_attributes(Ipopt.Optimizer, solver_attrs...)
        pm_out, converted_buses = solve_pf_with_controls(pm_data, optimizer, enforce_q_limits, q_limit_tolerance)
        term = string(pm_out["termination_status"])
        ok = term in ("LOCALLY_SOLVED", "OPTIMAL", "ALMOST_LOCALLY_SOLVED", "ALMOST_OPTIMAL")
        result["success"] = ok
        result["termination_status"] = term
        result["solve_time"] = sanitize_json_value(get(pm_out, "solve_time", nothing))
        result["raw_result"] = sanitize_json_value(pm_out)
        result["applied_controls"] = controls
        result["pv_to_pq_bus_ids"] = converted_buses
        result["slack_bus_ids"] = [Int(bus["bus_i"]) for bus in values(pm_data["bus"]) if bus["bus_type"] == 3]
    catch err
        result["termination_status"] = "exception"
        result["error"] = sprint(showerror, err)
        result["stacktrace"] = sprint(showerror, err, catch_backtrace())
    end
    return result
end

function run_pf_server()
    for line in eachline(stdin)
        result = try
            request = JSON3.read(line)
            solve_pf_request(request[:case], request[:payload])
        catch err
            Dict{String, Any}(
                "success" => false,
                "termination_status" => "server_exception",
                "solver_name" => "powermodels",
                "task" => "pf",
                "error" => sprint(showerror, err),
                "stacktrace" => sprint(showerror, err, catch_backtrace()),
            )
        end
        write(stdout, "PGDF_RESULT\t")
        JSON3.write(stdout, result)
        write(stdout, '\n')
        flush(stdout)
    end
end

if abspath(PROGRAM_FILE) == @__FILE__
    if length(ARGS) == 1 && ARGS[1] == "--server"
        run_pf_server()
    elseif length(ARGS) >= 3
        case_data = JSON3.read(read(ARGS[1], String))
        payload = JSON3.read(read(ARGS[2], String))
        result = solve_pf_request(case_data, payload)
        open(ARGS[3], "w") do io
            JSON3.write(io, result)
        end
    else
        error("usage: run_pf.jl <case_json> <payload_json> <out_json>")
    end
end
