# Build a PackageCompiler sysimage that bakes the AC-OPF solve path (PowerModels,
# Ipopt, JSON3) so the campaign no longer pays first-run compilation cost.
#
# Driven entirely by environment variables (set by configs/slurm/build_julia_sysimage.sh):
#   PGDF_SYSIMAGE_PROJECT   instantiated solve project (…/julia/lockfiles/<platform> or …/julia)
#   PGDF_SYSIMAGE_OUTPUT    target .so path (…/julia/sysimages/<platform>/pgdf_sysimage.so)
#   PGDF_SYSIMAGE_WORKLOAD  optional precompile workload (defaults to julia/precompile_workload.jl)
using Pkg

const HERE = @__DIR__

project = get(ENV, "PGDF_SYSIMAGE_PROJECT", "")
isempty(project) && error("PGDF_SYSIMAGE_PROJECT must point to the instantiated solve project.")

sysimage_path = get(ENV, "PGDF_SYSIMAGE_OUTPUT", "")
isempty(sysimage_path) && error("PGDF_SYSIMAGE_OUTPUT must be set to the target .so path.")

workload = get(ENV, "PGDF_SYSIMAGE_WORKLOAD", joinpath(HERE, "precompile_workload.jl"))
isfile(workload) || error("Precompile workload not found: $workload")

mkpath(dirname(sysimage_path))

# PackageCompiler lives in this build environment; the solve packages are pulled
# from PGDF_SYSIMAGE_PROJECT so the solve lockfiles stay untouched.
try
    @eval using PackageCompiler
catch
    Pkg.add("PackageCompiler")
    @eval using PackageCompiler
end

@info "Building sysimage" project sysimage_path workload
create_sysimage(
    [:PowerModels, :Ipopt, :JSON3];
    sysimage_path = sysimage_path,
    precompile_execution_file = workload,
    project = project,
)
@info "Sysimage build complete" sysimage_path
