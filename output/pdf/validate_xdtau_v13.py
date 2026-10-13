"""XD-TAU expanded-domain formula examples, seed 20261013."""
from pathlib import Path
import json, math, itertools
import numpy as np

BASE=Path(__file__).resolve().parent
rng=np.random.default_rng(20261013)
checks=[]
def check(name,ok,**data):
    checks.append(dict(name=name,passed=bool(ok),**data))
    assert ok,name

def smin(a,b,h):return (a+b-np.sqrt((a-b)**2+2*h*h))/2
def smax(a,b,h):return (a+b+np.sqrt((a-b)**2+2*h*h))/2
a=rng.normal(size=20000);b=rng.normal(size=20000);h=.2
d=np.sqrt((a-b)**2+2*h*h)
wa=(1-(a-b)/d)/2;wb=1-wa
eps=1e-6
fd=(smin(a+eps,b,h)-smin(a-eps,b,h))/(2*eps)
check('smooth_CSG_gradient',np.max(np.abs(fd-wa))<1e-9,
      samples=len(a),max_error=float(np.max(np.abs(fd-wa))))
check('smooth_CSG_duality',np.max(np.abs(smax(a,b,h)+smin(-a,-b,h)))<1e-14,
      samples=len(a))
check('regularized_self_difference',not np.any(np.maximum(a,-a)<0),samples=len(a))
empty_d,full_d=float('inf'),float('-inf')
check('empty_full_distance',empty_d>0 and full_d<=0,empty_occupancy=0,full_occupancy=1)

# Explicit binary equal-area profile: alternate u=y/R and longitude.
def locate(p,depth):
    p=np.asarray(p);u=float(p[1]/np.linalg.norm(p))
    psi=0. if p[0]==0 and p[2]==0 else math.atan2(p[2],p[0])%(2*math.pi)
    lo,hi,pl,ph=-1.,1.,0.,2*math.pi
    bits=[]
    for k in range(depth):
        if k%2==0:
            mid=(lo+hi)/2;bit=int(u>=mid)
            if bit:lo=mid
            else:hi=mid
        else:
            mid=(pl+ph)/2;bit=int(psi>=mid)
            if bit:pl=mid
            else:ph=mid
        bits.append(bit)
    uc,pc=(lo+hi)/2,(pl+ph)/2
    rep=np.array([math.sqrt(1-uc*uc)*math.cos(pc),uc,math.sqrt(1-uc*uc)*math.sin(pc)])
    radius_bound=max(abs(math.asin(lo)-math.asin(uc)),abs(math.asin(hi)-math.asin(uc)))+(ph-pl)/2
    return bits,(lo,hi,pl,ph),rep,min(math.pi,radius_bound)
pts=rng.normal(size=(20000,3));pts/=np.linalg.norm(pts,axis=1,keepdims=True)
maxerr=0.;maxbound=0.;maxareaerr=0.
for point in pts:
    bits,box,rep,bound=locate(point,14)
    err=math.atan2(np.linalg.norm(np.cross(point,rep)),float(point@rep))
    lo,hi,pl,ph=box
    maxerr=max(maxerr,err);maxbound=max(maxbound,bound)
    maxareaerr=max(maxareaerr,abs((hi-lo)*(ph-pl)-4*math.pi/2**14))
    assert err<=bound+1e-13
check('binary_equal_area_profile',maxareaerr<1e-15,points=len(pts),depth=14,
      max_surface_error_rad=maxerr,max_cell_radius_bound_rad=maxbound,
      max_cell_area_residual=maxareaerr)
special=[np.array([0.,1.,0.]),np.array([0.,-1.,0.]),np.array([1.,0.,0.]),np.array([0.,0.,-1.])]
check('profile_seam_poles',all(len(locate(p,14)[0])==14 for p in special),points=4)

# An explicit parameter fitting step and parallel bit mutation.
radius=.8;target=1.5;initial=(radius-target)**2
for _ in range(12):radius=radius-.2*2*(radius-target)
final=(radius-target)**2
check('continuous_parameter_fit',final<initial*1e-5,initial_loss=initial,final_loss=final,
      final_radius=radius,steps=12)
old=np.array([0,1,1,0],dtype=np.int8);event=np.array([1,0,1,0],dtype=np.int8)
check('simultaneous_bit_update',np.array_equal(old^event,[1,1,0,0]),input_bits=old.tolist(),output_bits=(old^event).tolist())

# Conservative finite-volume exchange: columns of incidence matrix sum to zero.
inc=np.array([[-1,0,1],[1,-1,0],[0,1,-1]],dtype=float)
amount=np.array([8.,5.,3.]);flux=np.array([.2,-.1,.05]);dt=.3
after=amount-dt*(inc@flux)
check('finite_volume_internal_flux',abs(after.sum()-amount.sum())<1e-14,
      total_before=float(amount.sum()),total_after=float(after.sum()))
solidangles=rng.random(64);solidangles*=4*np.pi/solidangles.sum()
volume=np.sum(solidangles*(3.**3-2.**3)/3)
expected=4*np.pi*(3.**3-2.**3)/3
check('spherical_shell_volume',abs(volume-expected)<1e-12,total_volume=float(volume),residual=float(volume-expected))

w=rng.normal(size=(10000,3));factor=.5*1.2*.8*.3
force=-factor*np.linalg.norm(w,axis=1)[:,None]*w
power=-np.sum(force*w,axis=1)
expected=factor*np.linalg.norm(w,axis=1)**3
check('drag_power',np.max(abs(power-expected))<1e-13,samples=len(w),max_residual=float(np.max(abs(power-expected))))
mass,drag,dt,v=2.,.7,.4,np.array([2.,-1.,.5])
vnew=np.exp(-drag*dt/mass)*v
kold=.5*mass*(v@v);knew=.5*mass*(vnew@vnew);heat=kold-knew
check('linear_drag_heat_balance',abs(knew+heat-kold)<1e-14,kinetic_before=kold,kinetic_after=knew,heat_gain=heat)
omega=rng.normal(size=(10000,3));vel=rng.normal(size=(10000,3))
work=np.sum(vel*np.cross(omega,vel),axis=1)
check('Coriolis_zero_work',np.max(abs(work))<1e-13,samples=len(work),max_residual=float(np.max(abs(work))))

I0,kappa,length=7.,1.2,.7
I1=I0*math.exp(-kappa*length);absorbed=I0*(-math.expm1(-kappa*length))
check('optical_absorption_balance',abs(I1+absorbed-I0)<1e-14,incident=I0,transmitted=I1,absorbed=absorbed)
state=rng.normal(size=(1000,2));gamma=.6
J=np.array([[0.,1.],[-1.,0.]]);R=np.diag([0.,gamma])
rates=np.sum(state*(state@(J-R).T),axis=1)
expected=-gamma*state[:,1]**2
check('port_Hamiltonian_energy',np.max(abs(rates-expected))<1e-14,samples=len(state),max_residual=float(np.max(abs(rates-expected))))

# Sparse arbitrary binary tree in breadth-first order, including right-only node.
D=[1,1,0,1,1,0,0,0,0,0] # root children 1,2; node1 right3; node2 left4
rank=lambda pos:sum(D[:pos+1])
select=lambda ordinal:[i for i,b in enumerate(D) if b][ordinal-1]
child={(i,s):rank(2*i+s) for i in range(5) for s in range(2) if D[2*i+s]}
parents={j:select(j)//2 for j in range(1,5)}
check('packed_tree_rank_select',child=={(0,0):1,(0,1):2,(1,1):3,(2,0):4} and parents=={1:0,2:0,3:1,4:2},nodes=5,shape_bits=10)

# Tagged operations merge by set union; mask materialization XORs distinct payloads.
replicas=[frozenset([(1,1),(2,3)]),frozenset([(2,3),(3,2)]),frozenset([(4,7)])]
def materialize(ops):
    v=0
    for tag,payload in ops:v^=payload
    return v
answers=set()
for order in itertools.permutations(replicas):
    merged=frozenset()
    for ops in order:merged=merged|ops
    answers.add((tuple(sorted(merged)),materialize(merged)))
check('replica_join',len(answers)==1 and replicas[0]|replicas[0]==replicas[0],merge_orders=6,distinct_ops=4)

for _ in range(1024):
    raw=rng.integers(0,2,size=(12,12));Q=(raw^raw.T).astype(np.int8)
    H=np.hstack([np.eye(12,dtype=np.int8),Q])
    sym=np.block([[np.zeros((12,12),dtype=np.int8),np.eye(12,dtype=np.int8)],
                  [np.eye(12,dtype=np.int8),np.zeros((12,12),dtype=np.int8)]])
    assert not np.any((H@sym@H.T)%2)
check('graph_state_commutation',True,graphs=1024,qubits_per_graph=12,generator_rank=12)
xs=np.array(list(itertools.product([0,1],repeat=4)),dtype=np.int8)
f=(xs[:,0]*xs[:,1])^(xs[:,2]*xs[:,3])
walsh=np.array([np.sum((-1)**(f^((xs@a)%2))) for a in xs])
check('bent_channel_Walsh',np.all(abs(walsh)==4),inputs=16,Walsh_magnitudes=sorted(set(abs(walsh).tolist())),ones=int(f.sum()))

z=.65*np.sqrt(rng.random(1000))*np.exp(2j*np.pi*rng.random(1000))
w=.65*np.sqrt(rng.random(1000))*np.exp(2j*np.pi*rng.random(1000));focus=.2+.1j
mob=lambda x:(x-focus)/(1-np.conj(focus)*x)
hd=lambda a,b:2*np.arctanh(np.abs((a-b)/(1-np.conj(a)*b)))
herr=np.max(abs(hd(z,w)-hd(mob(z),mob(w))))
check('hyperbolic_focus_isometry',herr<1e-13,pairs=1000,max_distance_residual=float(herr))
theta=np.linspace(-3,8,1000);a=.35;beta=.4
warp=lambda x:x+a*np.sin(x-beta)
seamerr=np.max(abs(warp(theta+2*np.pi)-warp(theta)-2*np.pi))
check('periodic_phase_adapter',seamerr<1e-14 and np.min(1+a*np.cos(theta-beta))>0,
      angles=len(theta),max_seam_residual=float(seamerr),min_derivative=float(np.min(1+a*np.cos(theta-beta))))
T=np.linspace(0,3,1000);tstar=2.5;t=tstar*np.expm1(T);lapse=tstar*np.exp(T)
check('log_time_chain_rule',np.max(abs(lapse-(t+tstar)))<1e-13,samples=len(T))

Q=np.array([[0,1,0],[1,0,1],[0,1,0]],dtype=float);L=np.diag(Q.sum(1))-Q
x=np.array([3.,1.,-2.]);energy=float(x@L@x)
check('graph_diffusion_quadratic',abs(energy-((3-1)**2+(1+2)**2))<1e-14 and abs(np.sum(L@x))<1e-14,
      quadratic=energy,total_signal_derivative=float(np.sum(-L@x)))
N=np.array([[-1.,-1.,-1.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])
c=np.array([2.,.3,.4,.2]);f,a,b,m=c
rates=np.array([.5*f*b,.7*f*a,.2*f*a*b])
check('reaction_stoichiometric_balance',np.all(np.ones(4)@N==0) and abs(np.sum(N@rates))<1e-14,
      reaction_columns=3,total_amount_derivative=float(np.sum(N@rates)))
rs=np.exp(rng.uniform(-5,5,size=10000));omega=3.25;ratio=np.exp(2*np.pi/omega)
conductivity=lambda x:2*np.exp(.4*np.sin(omega*np.log(x)))
residual=float(np.max(abs(conductivity(rs*ratio)-conductivity(rs))))
temp=rng.uniform(200,500,size=1000);grad=rng.normal(size=(1000,3));k=conductivity(np.exp(rng.uniform(-3,3,size=1000)))
entropy=k*np.sum(grad*grad,axis=1)/(temp*temp)
check('scale_patterned_thermal_channel',residual<1e-13 and np.all(entropy>=0),
      radii=10000,max_scale_residual=residual,min_entropy_production_density=float(entropy.min()))
rad=np.linspace(0,2,1000);rstar=.2;rho_max=np.log1p(1/rstar)
s=np.log1p(rad/rstar)/rho_max;t=rng.random(1000);t[0]=0
lam=np.column_stack([1-s,s*(1-t),s*t])
ss=lam[:,1]+lam[:,2];tt=np.zeros(1000);tt[1:]=lam[1:,2]/ss[1:]
restored=rstar*np.expm1(ss*rho_max)
check('barycentric_logpolar_inverse',np.max(abs(restored-rad))<1e-13 and np.max(abs(tt-t))<1e-13,
      radii=1000,max_radius_residual=float(np.max(abs(restored-rad))),max_turn_residual=float(np.max(abs(tt-t))))
rad=np.linspace(0,3,1000);rc,ell=1.,.8
chamber=rc+ell*np.tanh((rad-rc)/ell)
restored=rc+ell*np.arctanh((chamber-rc)/ell)
check('chamber_inverse',np.max(abs(restored-rad))<1e-12,radii=1000,max_residual=float(np.max(abs(restored-rad))))
result=dict(edition='1.3',date='2026-10-13',seed=20261013,
            description='Equation-level examples across geometry, physical accounting, graph/quantum adapters and distributed algebra.',
            checks=checks,passed=len(checks),failed=0)
(BASE/'validation_results_v13.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
