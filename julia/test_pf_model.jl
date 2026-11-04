using Test

include("run_pf.jl")

@testset "PF generator bus normalization" begin
    pm_data = Dict{String, Any}(
        "bus" => Dict(
            "1" => Dict("bus_i" => 1, "bus_type" => 3, "vm" => 1.0),
            "2" => Dict("bus_i" => 2, "bus_type" => 1, "vm" => 1.0),
            "3" => Dict("bus_i" => 3, "bus_type" => 1, "vm" => 1.0),
            "4" => Dict("bus_i" => 4, "bus_type" => 1, "vm" => 1.0),
            "5" => Dict("bus_i" => 5, "bus_type" => 2, "vm" => 1.02),
            "6" => Dict("bus_i" => 6, "bus_type" => 2, "vm" => 1.01),
        ),
        "gen" => Dict(
            "1" => Dict("gen_bus" => 2, "gen_status" => 1, "pmin" => 0.0, "pmax" => 10.0, "qmin" => -5.0, "qmax" => 5.0, "vg" => 1.04),
            "2" => Dict("gen_bus" => 3, "gen_status" => 1, "pmin" => 0.0, "pmax" => 0.0, "qmin" => 0.0, "qmax" => 0.0, "vg" => 1.0),
            "3" => Dict("gen_bus" => 4, "gen_status" => 0, "pmin" => 0.0, "pmax" => 10.0, "qmin" => -5.0, "qmax" => 5.0, "vg" => 1.0),
            "4" => Dict("gen_bus" => 5, "gen_status" => 1, "pmin" => 0.0, "pmax" => 10.0, "qmin" => -5.0, "qmax" => 5.0, "vg" => 1.02),
        ),
    )

    normalize_pf_generator_buses!(pm_data)

    @test pm_data["bus"]["1"]["bus_type"] == 3
    @test pm_data["bus"]["2"]["bus_type"] == 2
    @test pm_data["bus"]["2"]["vm"] == 1.04
    @test pm_data["bus"]["3"]["bus_type"] == 1
    @test pm_data["gen"]["2"]["gen_status"] == 0
    @test pm_data["bus"]["4"]["bus_type"] == 1
    @test pm_data["bus"]["5"]["bus_type"] == 2
    @test pm_data["bus"]["6"]["bus_type"] == 1
end