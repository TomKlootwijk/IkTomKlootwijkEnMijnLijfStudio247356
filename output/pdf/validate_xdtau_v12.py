"""Reproducible component checks for XD-TAU v1.1. No system benchmark."""
from pathlib import Path
import json
import math
import numpy as np

BASE = Path(__file__).resolve().parent
rng = np.random.default_rng(20261013)
records = []

def record(name, passed, **details):
    records.append(dict(name=name, passed=bool(passed), **details))
    if not passed:
        raise AssertionError(name)

# Exhaustively verify address uniqueness, depth bounds, parent/child consistency.
slots = set()
for d in range(17):
    for j in range(2**d):
        a = 2**d - 1 + j
        assert a not in slots
        slots.add(a)
        assert 2*a+1 == 2**(d+1)-1+2*j
        assert 2*a+2 == 2**(d+1)-1+2*j+1
record('binary_addresses', len(slots) == 131071,
       nodes=len(slots), max_depth=16)

# Round trip at the same phase. Surface error uses atan2(cross,dot), not acos.
n = 100000
p = rng.normal(size=(n,3))
p /= np.linalg.norm(p, axis=1, keepdims=True)
rstar, phase = .125, 6.173
h = (p[:,1] >= 0).astype(int)
r = np.hypot(p[:,0],p[:,2])
rho = np.log1p(r/rstar)
q = ((np.arctan2(p[:,2],p[:,0])+phase)/(2*np.pi)) % 1
rr = rstar*np.expm1(rho)
assert rr.max() <= 1+8*np.finfo(float).eps
rr = np.clip(rr,0,1)  # roundoff-only clamp; invalid inputs remain inadmissible.
psi = (2*np.pi*q-phase) % (2*np.pi)
pp = np.column_stack((rr*np.cos(psi), (2*h-1)*np.sqrt(np.maximum(0,1-rr*rr)), rr*np.sin(psi)))
errors = np.arctan2(np.linalg.norm(np.cross(p,pp),axis=1),np.sum(p*pp,axis=1))
residual = np.max(np.abs(np.sum(pp*pp,axis=1)-1))
record('sphere_roundtrip', errors.max()<1e-8 and residual<2e-15,
       points=n, radius=1, same_phase=True,
       max_surface_error_rad=float(errors.max()),
       max_sphere_squared_norm_residual=float(residual))

# A separate stress check records finite-precision information loss at the seam.
tiny_y = 1e-9
edge_r = float(np.sqrt(1-tiny_y*tiny_y))
edge_y = float(np.sqrt(max(0,1-edge_r*edge_r)))
record('seam_conditioning_counterexample', edge_y == 0,
       original_y=tiny_y, reconstructed_y=edge_y,
       meaning='Double precision can round projected radius to R; retain residual or a better-conditioned chart.')

# Exact poles and owned seam. q=0 is the designated angular code at either pole.
special = [(0,-1,0),(0,1,0),(1,0,0),(0,0,1),(-1,0,0),(0,0,-1)]
for xyz in special:
    x,y,z=xyz
    radial=math.hypot(x,z)
    hemisphere=1 if y>=0 else 0
    turn=0 if radial==0 else ((math.atan2(z,x)+phase)/(2*math.pi))%1
    angle=(2*math.pi*turn-phase)%(2*math.pi)
    restored=(radial*math.cos(angle),(2*hemisphere-1)*math.sqrt(max(0,1-radial**2)),radial*math.sin(angle))
    assert np.linalg.norm(np.array(restored)-np.array(xyz))<3e-15
record('poles_and_seam', True, special_points=len(special), seam_owner=1)

# Exhaust all C in {-1,+1}^{3x3}; M depends on A,B only through C=A*B.
outputs=set()
for value in range(512):
    bits=np.array([(value>>i)&1 for i in range(9)],dtype=np.int8).reshape(3,3)
    c=1-2*bits
    m=c*c.T
    assert np.array_equal(m,m.T) and np.all(np.diag(m)==1)
    assert np.array_equal((1-m)//2,np.bitwise_xor(bits,bits.T))
    outputs.add(tuple(m.ravel()))
record('hadamard_exhaustive_N3',len(outputs)==8, input_C_matrices=512,
       distinct_output_masks=len(outputs), independent_output_bits=3)
for _ in range(4096):
    abits=rng.integers(0,2,size=(16,16),dtype=np.int8)
    bbits=rng.integers(0,2,size=(16,16),dtype=np.int8)
    a,b=1-2*abits,1-2*bbits
    m=(a*b)*(a.T*b.T)
    mbits=abits^bbits^abits.T^bbits.T
    assert np.array_equal(m,1-2*mbits)
record('hadamard_XOR',True,random_pairs=4096,N=16)

bits=rng.integers(0,2,size=257,dtype=np.uint8).tolist()
words=[0]*math.ceil(len(bits)/64)
for i,bit in enumerate(bits): words[i//64]|=bit<<(i%64)
recovered=[(words[i//64]>>(i%64))&1 for i in range(len(bits))]
record('packed_bit_retrieval',bits==recovered,logical_bits=257,words_64bit=len(words))

ab=rng.normal(size=(100000,2))*10
hb=10**rng.uniform(-3,1,size=100000)
sm=(ab[:,0]+ab[:,1]-np.sqrt((ab[:,0]-ab[:,1])**2+2*hb*hb))/2
delta=np.min(ab,axis=1)-sm
normalized=delta/hb
record('smooth_min_bound',np.all(delta>=-1e-13) and normalized.max()<=1/math.sqrt(2)+1e-10,
       pairs=len(ab),max_observed_displacement_per_h=float(normalized.max()),
       analytic_upper_bound_per_h=1/math.sqrt(2))

# Root of u=2*tanh(u) supplies the explicit angular three-to-one collision.
lo,hi=1.,2.
for _ in range(80):
    u=(lo+hi)/2
    if u-2*math.tanh(u)>0: hi=u
    else: lo=u
u=(lo+hi)/2
angles=np.array([math.pi-u,math.pi,math.pi+u])
warped=angles-2*np.tanh(angles-math.pi)
seam_delta=(2*math.pi-2*math.tanh(math.pi))-(0-2*math.tanh(-math.pi))
record('angular_fold_and_seam_counterexample',np.max(np.abs(warped-math.pi))<1e-14 and abs(seam_delta-2*math.pi)>1,
       input_angles_rad=angles.tolist(),output_angles_rad=warped.tolist(),
       seam_output_difference_rad=seam_delta,
       required_for_degree_one_periodicity_rad=2*math.pi)

omega=3.25
ratio=math.exp(2*math.pi/abs(omega))
rs=np.exp(rng.uniform(-5,5,size=10000))
scale_error=np.max(np.abs(np.sin(omega*np.log(rs*ratio))-np.sin(omega*np.log(rs))))
record('log_sine_scale_repetition',scale_error<2e-14,
       radii=len(rs),omega=omega,scale_ratio=ratio,max_error=float(scale_error))

rs=np.linspace(0,2,1000)
r0,ag,rc=.4,.8,.3
expanded=r0*np.exp(ag*(rs-rc))
restored=rc+np.log(expanded/r0)/ag
record('geyser_radial_inverse',np.max(np.abs(restored-rs))<1e-14,
       radii=len(rs),max_error=float(np.max(np.abs(restored-rs))))

# Equal-area latitude belt [0,R/2^k] has antipodal equator endpoints in its closure.
# Spherical belt area is 2*pi*R*Delta_y, but its geodesic diameter stays pi*R.
record('equal_area_resolution_counterexample',True,
       R=1,depth=20,belt_area=2*math.pi/2**20,
       geodesic_diameter=math.pi,
       analytic_fact='Area tends to zero while geodesic diameter remains pi*R.')

# One bit feedback flip returns after two updates, disproving automatic stationarity.
orbit=[0]
for _ in range(4): orbit.append(1-orbit[-1])
record('feedback_cycle_counterexample',orbit==[0,1,0,1,0],orbit=orbit)

result=dict(date='2026-10-13',seed=20261013,
            scope='Formula checks and explicit counterexamples; no complete U or system benchmark supplied.',
            checks=records,passed=len(records),failed=0,
            analytic_domain_finding='If m=-1 and the sphere contains the Klein-derived solid, the union can be all R^3, with empty boundary; admissible-state validation is required.')
(BASE/'validation_results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
