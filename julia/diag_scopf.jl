# SCOPF infeasibility diagnostic: base AC-OPF, then each contingency alone under
# variants of build_c1_scopf with constraint groups disabled.
# usage: diag_scopf.jl <resolved_case.json> <resolved_contingency_set.json> <out.json>
include("run_batch.jl")
const PMSC = PowerModelsSecurityConstrained
const _PM = PowerModels

function build_diag_scopf(pm::_PM.AbstractPowerModel; link_vm=true, cont_thermal=true, cont_angle=true)
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

function make_optimizer()
    attrs = Pair{String,Any}["print_level" => 0, "sb" => "yes", "tol" => 1e-6, "max_iter" => 3000]
    ls = strip(get(ENV, "IPOPT_LINEAR_SOLVER", ""))
    !isempty(ls) && push!(attrs, "linear_solver" => String(ls))
    hl = strip(get(ENV, "IPOPT_HSL_LIBRARY", ""))
    !isempty(hl) && push!(attrs, "hsllib" => String(hl))
    return optimizer_with_attributes(Ipopt.Optimizer, attrs...)
end

function run_variant(pm_data, case_data, conts, name; kw...)
    data = deepcopy(pm_data)
    add_scopf_contingencies!(data, case_data, conts)
    mn = build_c1_scopf_multinetwork(data)
    t = @elapsed out = _PM.solve_model(mn, ACPPowerModel, make_optimizer(),
        pm -> build_diag_scopf(pm; kw...); multinetwork=true)
    term = string(out["termination_status"])
    obj = get(out, "objective", nothing)
    println("  ", rpad(name, 22), rpad(term, 22), " obj=", obj, " t=", round(t; digits=1))
    flush(stdout)
    return Dict("variant" => name, "termination_status" => term,
                "objective" => sanitize_json_value(obj), "time_s" => t)
end

case_data = JSON3.read(read(ARGS[1], String))
cset = JSON3.read(read(ARGS[2], String))
pm_data = to_powermodels_data(case_data)
report = Dict{String,Any}("base_opf" => nothing, "contingencies" => Any[])

base = solve_opf(deepcopy(pm_data), ACPPowerModel, make_optimizer())
println("BASE AC-OPF: ", base["termination_status"], " obj=", base["objective"])
report["base_opf"] = Dict("termination_status" => string(base["termination_status"]),
                          "objective" => base["objective"])

variants = [
    ("full", (;)),
    ("no_vm_link", (; link_vm=false)),
    ("no_cont_thermal", (; cont_thermal=false)),
    ("no_cont_angle", (; cont_angle=false)),
    ("no_vm_no_thermal", (; link_vm=false, cont_thermal=false)),
]
for c in cset[:contingencies]
    println("CONT ", c[:contingency_id])
    rows = Any[]
    for (name, kw) in variants
        try
            push!(rows, run_variant(pm_data, case_data, [c], name; kw...))
        catch err
            println("  ", name, " ERROR ", sprint(showerror, err))
            push!(rows, Dict("variant" => name, "error" => sprint(showerror, err)))
        end
    end
    push!(report["contingencies"], Dict("contingency_id" => String(c[:contingency_id]), "results" => rows))
end
open(ARGS[3], "w") do io
    JSON3.write(io, report)
end
