"""Tests for krotov.Objective in isolation"""
import os
import copy

import numpy as np
import scipy
import qutip

import krotov

import pytest


@pytest.fixture
def transmon_ham_and_states(
        Ec=0.386, EjEc=45, nstates=2, ng=0.0, T=10.0):
    """Transmon Hamiltonian"""
    Ej = EjEc * Ec
    n = np.arange(-nstates, nstates+1)
    up = np.diag(np.ones(2*nstates), k=-1)
    do = up.T
    H0 = qutip.Qobj(np.diag(4*Ec*(n - ng)**2) - Ej*(up+do)/2.0)
    H1 = qutip.Qobj(-2*np.diag(n))

    eigenvals, eigenvecs = scipy.linalg.eig(H0.full())
    ndx = np.argsort(eigenvals.real)
    E = eigenvals[ndx].real
    V = eigenvecs[:, ndx]
    w01 = E[1]-E[0]  # Transition energy between states

    psi0 = qutip.Qobj(V[:, 0])
    psi1 = qutip.Qobj(V[:, 1])

    profile = lambda t: np.exp(-40.0*(t/T - 0.5)**2)
    eps0 = lambda t, args: 0.5 * profile(t) * np.cos(8*np.pi*w01*t)
    return ([H0, [H1, eps0]], psi0, psi1)


def test_krotov_objective_initialization(transmon_ham_and_states):
    """Test basic instantiation of a krotov.Objective with qutip objects"""
    H, psi0, psi1 = transmon_ham_and_states
    target = krotov.Objective(H, psi0, psi1)
    assert target.H == H
    assert target.initial_state == psi0
    assert target.target_state == psi1
    assert target == krotov.Objective(
        H=H, initial_state=psi0, target_state=psi1)


def test_objective_copy(transmon_ham_and_states):
    """Test that copy.copy(objective) produces the expected equalities by value
    and by reference"""
    H, psi0, psi1 = transmon_ham_and_states
    c1 = H[1].copy()  # we just need something structurally sound ...
    c2 = H[1].copy()  # ... It doesn't need to make sense physically
    assert c1 == c2      # equal by value
    assert c1 is not c2  # not equal by reference

    target1 = krotov.Objective(H, psi0, psi1, c_ops=[c1, c2])
    target2 = copy.copy(target1)
    assert target1 == target2
    assert target1 is not target2
    assert target1.H == target2.H
    assert target1.H is not target2.H
    assert target1.H[0] is target2.H[0]
    assert target1.H[1] is not target2.H[1]
    assert target1.H[1][0] is target2.H[1][0]
    assert target1.H[1][1] is target2.H[1][1]
    assert target1.c_ops[0] == target2.c_ops[0]
    assert target1.c_ops[0] is not target2.c_ops[0]
    assert target1.c_ops[0][0] is target2.c_ops[0][0]
    assert target1.c_ops[0][1] is target2.c_ops[0][1]


def test_adoint_objective(transmon_ham_and_states):
    """Test taking the adjoint of an objective"""
    H, psi0, psi1 = transmon_ham_and_states
    target = krotov.Objective(H, psi0, psi1)
    adjoint_target = target.adjoint
    assert isinstance(adjoint_target.H, list)
    assert isinstance(adjoint_target.H[0], qutip.Qobj)
    assert isinstance(adjoint_target.H[1], list)
    assert isinstance(adjoint_target.H[1][0], qutip.Qobj)
    assert (adjoint_target.H[0] - target.H[0]).norm() < 1e-12
    assert (adjoint_target.H[1][0] - target.H[1][0]).norm() < 1e-12
    assert adjoint_target.H[1][1] == target.H[1][1]
    assert adjoint_target.initial_state.isbra
    assert adjoint_target.target_state.isbra


@pytest.fixture
def tlist_control(request):
    testdir = os.path.splitext(request.module.__file__)[0]
    tlist, control = np.genfromtxt(
        os.path.join(testdir, 'pulse.dat'), unpack=True)
    return tlist, control


def test_objective_mesolve_propagate(transmon_ham_and_states, tlist_control):
    """Test propagation method of objective"""
    tlist, control = tlist_control
    H, psi0, psi1 = transmon_ham_and_states
    H = copy.deepcopy(H)
    T = tlist[-1]
    nt = len(tlist)
    H[1][1] = lambda t, args: (
        0 if (t > float(T)) else
        control[int(round(float(nt-1) * (t/float(T))))])
    target = krotov.Objective(H, psi0, psi1)

    assert len(tlist) == len(control) > 0

    res1 = target.mesolve(tlist)
    res2 = target.propagate(tlist, propagator=krotov.propagators.expm)
    assert len(res1.states) == len(res2.states) == len(tlist)
    assert (1 - np.abs(res1.states[-1].overlap(res2.states[-1]))) < 1e-4

    P0 = psi0 * psi0.dag()
    P1 = psi1 * psi1.dag()
    e_ops = [P0, P1]

    res1 = target.mesolve(tlist, e_ops=e_ops)
    res2 = target.propagate(
        tlist, e_ops=e_ops, propagator=krotov.propagators.expm)

    assert len(res1.states) == len(res2.states) == 0
    assert len(res1.expect) == len(res2.expect) == 2
    assert len(res1.expect[0]) == len(res2.expect[0]) == len(tlist)
    assert len(res1.expect[1]) == len(res2.expect[1]) == len(tlist)
    assert abs(res1.expect[0][-1] - res2.expect[0][-1]) < 1e-2
    assert abs(res1.expect[1][-1] - res2.expect[1][-1]) < 1e-2
    assert abs(res1.expect[0][-1] - 0.1925542) < 1e-7
    assert abs(res1.expect[1][-1] - 0.7595435) < 1e-7


def test_plug_in_array_controls_as_func():
    """Test _plug_in_array_controls_as_func, specifically that it generates a
    function that switches between the points in tlist"""
    nt = 4
    T = 5.0
    u1 = np.random.random(nt)
    u2 = np.random.random(nt)
    H = ['H0', ['H1', u1], ['H2', u2]]
    controls = [u1, u2]
    mapping = [
        [1, ],  # u1
        [2, ],  # u2
    ]
    tlist = np.linspace(0, T, nt)
    H_with_funcs = krotov.objective._plug_in_array_controls_as_func(
        H, controls, mapping, tlist)
    assert callable(H_with_funcs[1][1])
    assert callable(H_with_funcs[2][1])

    u1_func = H_with_funcs[1][1]
    assert u1_func(T + 0.1, None) == 0
    assert u1_func(T, None) == u1[-1]
    assert u1_func(0, None) == u1[0]
    dt = tlist[1] - tlist[0]
    assert u1_func(tlist[2] + 0.4 * dt, None) == u1[2]
    assert u1_func(tlist[2] + 0.6 * dt, None) == u1[3]

    u2_func = H_with_funcs[2][1]
    assert u2_func(T + 0.1, None) == 0
    assert u2_func(T, None) == u2[-1]
    assert u2_func(0, None) == u2[0]
    dt = tlist[1] - tlist[0]
    assert u2_func(tlist[2] + 0.4 * dt, None) == u2[2]
    assert u2_func(tlist[2] + 0.6 * dt, None) == u2[3]
