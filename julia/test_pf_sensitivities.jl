using LinearAlgebra
using SparseArrays
using Test

include("run_pf_sensitivity_pilot.jl")

function two_bus_case()
    return Dict(
        :case_id => "sensitivity_two_bus",
        :base_mva => 100.0,
        :buses => [
            Dict(:bus_id => "1", :type => 3, :vm => 1.01, :va => 0.0, :vmin => 0.9, :vmax => 1.1),
            Dict(:bus_id => "2", :type => 2, :vm => 1.0, :va => -0.04, :vmin => 0.9, :vmax => 1.1),
        ],
        :loads => [Dict(:load_id => "l1", :bus_id => "2", :pd => 90.0, :qd => 20.0)],
        :generators => [
            Dict(:gen_id => "slack", :bus_id => "1", :pg => 50.0, :qg => 10.0, :vg => 1.01, :status => 1, :pmin => 0.0, :pmax => 100.0, :qmin => -50.0, :qmax => 50.0),
            Dict(:gen_id => "g2", :bus_id => "2", :pg => 40.0, :qg => 10.0, :vg => 1.0, :status => 1, :pmin => 0.0, :pmax => 100.0, :qmin => -50.0, :qmax => 50.0),
        ],
        :branches => [Dict(:branch_id => "b1", :from => "1", :to => "2", :r => 0.01, :x => 0.1, :b => 0.0, :rate_a => 100.0, :status => 1)],
    )
end

function residual_at(pm_data, state)
    PFSensitivities.linearize(pm_data; state=state, control_names=String[]).residual
end

@testset "fixed-mode sparse PF sensitivities" begin
    pm_data = to_powermodels_data(two_bus_case())
    normalize_pf_generator_buses!(pm_data)
    model = PFSensitivities.linearize(pm_data)
    step = 1e-6
    numeric_jacobian = zeros(size(model.jacobian))
    for column in eachindex(model.state)
        plus = copy(model.state)
        minus = copy(model.state)
        plus[column] += step
        minus[column] -= step
        numeric_jacobian[:, column] = (residual_at(pm_data, plus) - residual_at(pm_data, minus)) / (2step)
    end
    @test Matrix(model.jacobian) ≈ numeric_jacobian rtol=1e-7 atol=1e-8
    @test model.controls == ["pg:g2", "vg:bus:2"]
    @test model.control_ids == [2, 2]
    @test model.reference_bus_id == 1
    @test model.control_bus_ids == [2, 2]
    @test norm(model.jacobian * model.sensitivity + model.input_jacobian) < 1e-12
    @test issparse(model.jacobian)

    pg_step_mw = 1e-3
    pg_plus = deepcopy(pm_data)
    pg_minus = deepcopy(pm_data)
    pg_plus["gen"]["2"]["pg"] += pg_step_mw / pm_data["baseMVA"]
    pg_minus["gen"]["2"]["pg"] -= pg_step_mw / pm_data["baseMVA"]
    numeric_pg = (residual_at(pg_plus, model.state) - residual_at(pg_minus, model.state)) / (2pg_step_mw)
    @test numeric_pg ≈ Array(model.input_jacobian[:, 1]) rtol=1e-8 atol=1e-10

    vg_step = 1e-6
    vg_plus = deepcopy(pm_data)
    vg_minus = deepcopy(pm_data)
    vg_plus["gen"]["2"]["vg"] += vg_step
    vg_minus["gen"]["2"]["vg"] -= vg_step
    numeric_vg = (residual_at(vg_plus, model.state) - residual_at(vg_minus, model.state)) / (2vg_step)
    @test numeric_vg ≈ Array(model.input_jacobian[:, 2]) rtol=1e-8 atol=1e-10
    @test finite_difference_check(pm_data, model) < 1e-8
end

@testset "pilot topology and provenance helpers" begin
    source = Dict("buses" => [Dict("bus_id" => "1")])
    mutable_copy = mutable_symbol_data(source)
    mutable_copy[:buses][1][:bus_id] = "2"
    @test source["buses"][1]["bus_id"] == "1"
    @test_throws ArgumentError prepare_record(Dict(:success => false), Dict{String, Any}())
end