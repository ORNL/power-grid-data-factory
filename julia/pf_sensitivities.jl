module PFSensitivities

using LinearAlgebra
using PowerModels
using SparseArrays

function _specified_injections(pm_data, bus_ids)
    base_mva = Float64(pm_data["baseMVA"])
    p = Dict(bus_id => 0.0 for bus_id in bus_ids)
    q = Dict(bus_id => 0.0 for bus_id in bus_ids)
    for load in values(pm_data["load"])
        load["status"] == 0 && continue
        bus_id = Int(load["load_bus"])
        p[bus_id] -= Float64(load["pd"])
        q[bus_id] -= Float64(load["qd"])
    end
    for gen in values(pm_data["gen"])
        gen["gen_status"] == 0 && continue
        bus_id = Int(gen["gen_bus"])
        p[bus_id] += Float64(gen["pg"])
        q[bus_id] += Float64(gen["qg"])
    end
    return p, q, base_mva
end

function _control_columns(pm_data, reference_bus, pv_buses, control_names)
    controls = NamedTuple[]
    for gen in sort(collect(values(pm_data["gen"])); by=gen -> Int(gen["index"]))
        gen["gen_status"] == 0 && continue
        bus_id = Int(gen["gen_bus"])
        bus_id == reference_bus && continue
        push!(controls, (name="pg:$(gen["source_id"][3])", kind=:pg, id=Int(gen["index"]), bus=bus_id))
    end
    for bus_id in pv_buses
        push!(controls, (name="vg:bus:$(bus_id)", kind=:vg, id=bus_id, bus=bus_id))
    end
    if control_names !== nothing
        requested = String.(control_names)
        available = Set(control.name for control in controls)
        unknown = setdiff(Set(requested), available)
        isempty(unknown) || throw(ArgumentError("Unknown fixed-mode control(s): $(join(sort(collect(unknown)), ", "))"))
        by_name = Dict(control.name => control for control in controls)
        controls = [by_name[name] for name in requested]
    end
    return controls
end

function linearize(pm_data; state=nothing, control_names=nothing)
    admittance = PowerModels.calc_admittance_matrix(pm_data)
    bus_ids = admittance.idx_to_bus
    bus_to_idx = admittance.bus_to_idx
    bus_types = Dict(bus_id => Int(pm_data["bus"][string(bus_id)]["bus_type"]) for bus_id in bus_ids)
    reference_buses = [bus_id for bus_id in bus_ids if bus_types[bus_id] == 3]
    length(reference_buses) == 1 || throw(ArgumentError("Fixed-mode linearization requires exactly one reference bus"))
    reference_bus = only(reference_buses)
    nonreference_buses = [bus_id for bus_id in bus_ids if bus_id != reference_bus]
    pq_buses = [bus_id for bus_id in nonreference_buses if bus_types[bus_id] == 1]
    pv_buses = [bus_id for bus_id in nonreference_buses if bus_types[bus_id] == 2]
    length(pq_buses) + length(pv_buses) == length(nonreference_buses) ||
        throw(ArgumentError("Unsupported bus type in fixed-mode linearization"))

    variable_count = 2 * length(nonreference_buses)
    z = if state === nothing
        vcat(
            [Float64(pm_data["bus"][string(bus_id)]["va"]) for bus_id in nonreference_buses],
            [Float64(pm_data["bus"][string(bus_id)]["vm"]) for bus_id in nonreference_buses],
        )
    else
        Float64.(state)
    end
    length(z) == variable_count || throw(ArgumentError("State vector has wrong dimension"))

    angle_col = Dict(bus_id => i for (i, bus_id) in enumerate(nonreference_buses))
    vm_col = Dict(bus_id => length(nonreference_buses) + i for (i, bus_id) in enumerate(nonreference_buses))
    p_rows = nonreference_buses
    q_rows = pq_buses
    equation_count = length(p_rows) + length(q_rows) + length(pv_buses)
    equation_count == variable_count || throw(ArgumentError("Fixed-mode equations are not square"))
    p_row = Dict(bus_id => i for (i, bus_id) in enumerate(p_rows))
    q_offset = length(p_rows)
    q_row = Dict(bus_id => q_offset + i for (i, bus_id) in enumerate(q_rows))
    v_offset = q_offset + length(q_rows)
    v_row = Dict(bus_id => v_offset + i for (i, bus_id) in enumerate(pv_buses))

    angles = Dict(reference_bus => Float64(pm_data["bus"][string(reference_bus)]["va"]))
    magnitudes = Dict(reference_bus => Float64(pm_data["bus"][string(reference_bus)]["vm"]))
    for (i, bus_id) in enumerate(nonreference_buses)
        angles[bus_id] = z[i]
        magnitudes[bus_id] = z[length(nonreference_buses) + i]
        magnitudes[bus_id] > 0 || throw(ArgumentError("Bus voltage magnitudes must be positive"))
    end
    voltage = [magnitudes[bus_id] * cis(angles[bus_id]) for bus_id in bus_ids]
    ybus = admittance.matrix
    current = ybus * voltage
    calculated = voltage .* conj.(current)
    p_calc = real.(calculated)
    q_calc = imag.(calculated)
    p_spec, q_spec, base_mva = _specified_injections(pm_data, bus_ids)

    residual = zeros(equation_count)
    for bus_id in p_rows
        residual[p_row[bus_id]] = p_calc[bus_to_idx[bus_id]] - p_spec[bus_id]
    end
    for bus_id in q_rows
        residual[q_row[bus_id]] = q_calc[bus_to_idx[bus_id]] - q_spec[bus_id]
    end
    vg_by_bus = Dict{Int, Float64}()
    for gen in values(pm_data["gen"])
        gen["gen_status"] == 0 && continue
        bus_id = Int(gen["gen_bus"])
        haskey(vg_by_bus, bus_id) && !isapprox(vg_by_bus[bus_id], Float64(gen["vg"]); atol=1e-10, rtol=0) &&
            throw(ArgumentError("Online generators at bus $(bus_id) have inconsistent voltage setpoints"))
        vg_by_bus[bus_id] = Float64(gen["vg"])
    end
    for bus_id in pv_buses
        haskey(vg_by_bus, bus_id) || throw(ArgumentError("PV bus $(bus_id) has no online generator voltage setpoint"))
        residual[v_row[bus_id]] = magnitudes[bus_id] - vg_by_bus[bus_id]
    end

    jacobian_rows = Int[]
    jacobian_cols = Int[]
    jacobian_values = Float64[]
    gbus = real.(ybus)
    bbus = imag.(ybus)
    y_rows, y_cols, _ = findnz(ybus)
    for edge in eachindex(y_rows)
        i = y_rows[edge]
        j = y_cols[edge]
        bus_id = bus_ids[i]
        other_id = bus_ids[j]
        haskey(p_row, bus_id) || continue
        vi = magnitudes[bus_id]
        vj = magnitudes[other_id]
        delta = angles[bus_id] - angles[other_id]
        p_equation_row = p_row[bus_id]
        if bus_id == other_id
            push!(jacobian_rows, p_equation_row); push!(jacobian_cols, angle_col[bus_id])
            push!(jacobian_values, -q_calc[i] - bbus[i, i] * vi^2)
            push!(jacobian_rows, p_equation_row); push!(jacobian_cols, vm_col[bus_id])
            push!(jacobian_values, p_calc[i] / vi + gbus[i, i] * vi)
        else
            if haskey(angle_col, other_id)
                push!(jacobian_rows, p_equation_row); push!(jacobian_cols, angle_col[other_id])
                push!(jacobian_values, vi * vj * (gbus[i, j] * sin(delta) - bbus[i, j] * cos(delta)))
            end
            if haskey(vm_col, other_id)
                push!(jacobian_rows, p_equation_row); push!(jacobian_cols, vm_col[other_id])
                push!(jacobian_values, vi * (gbus[i, j] * cos(delta) + bbus[i, j] * sin(delta)))
            end
        end
        if haskey(q_row, bus_id)
            q_equation_row = q_row[bus_id]
            if bus_id == other_id
                push!(jacobian_rows, q_equation_row); push!(jacobian_cols, angle_col[bus_id])
                push!(jacobian_values, p_calc[i] - gbus[i, i] * vi^2)
                push!(jacobian_rows, q_equation_row); push!(jacobian_cols, vm_col[bus_id])
                push!(jacobian_values, q_calc[i] / vi - bbus[i, i] * vi)
            else
                if haskey(angle_col, other_id)
                    push!(jacobian_rows, q_equation_row); push!(jacobian_cols, angle_col[other_id])
                    push!(jacobian_values, -vi * vj * (gbus[i, j] * cos(delta) + bbus[i, j] * sin(delta)))
                end
                if haskey(vm_col, other_id)
                    push!(jacobian_rows, q_equation_row); push!(jacobian_cols, vm_col[other_id])
                    push!(jacobian_values, vi * (gbus[i, j] * sin(delta) - bbus[i, j] * cos(delta)))
                end
            end
        end
    end
    for bus_id in pv_buses
        push!(jacobian_rows, v_row[bus_id]); push!(jacobian_cols, vm_col[bus_id]); push!(jacobian_values, 1.0)
    end
    jacobian = sparse(jacobian_rows, jacobian_cols, jacobian_values, equation_count, variable_count)

    controls = _control_columns(pm_data, reference_bus, pv_buses, control_names)
    input_rows = Int[]
    input_cols = Int[]
    input_values = Float64[]
    for (column, control) in enumerate(controls)
        if control.kind == :pg
            push!(input_rows, p_row[control.bus]); push!(input_cols, column); push!(input_values, -1.0 / base_mva)
        else
            push!(input_rows, v_row[control.bus]); push!(input_cols, column); push!(input_values, -1.0)
        end
    end
    input_jacobian = sparse(input_rows, input_cols, input_values, equation_count, length(controls))
    sensitivity = isempty(controls) ? zeros(variable_count, 0) : (lu(jacobian) \ Matrix(-input_jacobian))
    return (
        state=z,
        residual=residual,
        jacobian=jacobian,
        input_jacobian=input_jacobian,
        controls=[control.name for control in controls],
        control_ids=[control.id for control in controls],
        sensitivity=sensitivity,
        bus_ids=bus_ids,
        reference_bus_id=reference_bus,
        control_bus_ids=[control.bus for control in controls],
        control_kinds=[String(control.kind) for control in controls],
        state_outputs=(
            angle_bus_ids=nonreference_buses,
            voltage_bus_ids=nonreference_buses,
        ),
    )
end

end