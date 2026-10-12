# RB.py

from param import delta_t, time_steps
from param import (
    P_nom_pv, P_max_gen, P_min_gen, C_bss, SOC_min_bss, SOC_max_bss,
    eff_bss, P_nom_bss, COP_hp, C_hp
)
from param import (
    C_ev, eff_ev, SOC_min_ev, SOC_max_ev, P_nom_ev, SOC_target_ev, P_max_hp
)
import simulator as run


# =============================================================================
# SCENARIO SELECTION
# =============================================================================
# Choose which predefined scenario to simulate.

Scenario = 'S1'  # Default scenario; override from the command line: python rule_based.py S3


# =============================================================================
# NUMERICAL / CONTROL TUNING CONSTANTS
# =============================================================================
# Small tolerance used to avoid numerical issues when comparing values to zero.
EPS = 1e-9

# Thermal comfort controller tuning.
TEMP_DEADBAND = 0.5   # [°C] no heating if already close to setpoint
HP_GAIN = 0.7            # [-] softens one-step thermal correction
SMALL_ERR_BAND = 1.0     # [°C] threshold for gentler correction
SMALL_ERR_GAIN = 0.5     # [-] extra reduction close to setpoint


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def clamp(x, xmin, xmax):
    """
    Restrict x to the interval [xmin, xmax].

    Used to enforce physical limits such as:
    - battery charge/discharge power limits
    - EV charge/discharge power limits
    - generator minimum and maximum power
    - heat pump power limits
    """
    return max(xmin, min(x, xmax))


def hp_power(T_set, T_hp, P_loss):
    """
    Model-based heat pump controller.

    The heat pump is treated as part of essential demand. The controller estimates
    the electrical HP power needed to move the indoor temperature toward the
    setpoint while avoiding aggressive overshoot.

    Main logic:
    1. If temperature is already within the deadband, no heating is requested.
    2. Otherwise, a one-step heating request is calculated from the thermal model.
    3. The request is softened using HP_GAIN.
    4. If the temperature error is small, the request is reduced further.
    """

    if T_hp >= T_set - TEMP_DEADBAND:
        return 0.0

    p_req = ((((T_set - T_hp) * C_hp) / delta_t) + P_loss) / COP_hp

    p_req *= HP_GAIN

    temp_error = T_set - T_hp
    if 0.0 < temp_error < SMALL_ERR_BAND:
        p_req *= SMALL_ERR_GAIN

    return clamp(p_req, 0.0, P_max_hp)


def ev_charge_request(EV_connected, SOC_ev, EV_remaining_time):
    """
    Compute the EV charging request [kW].

    The EV target is flexible, not essential. This function only calculates the
    charging power required to reach SOC_target_ev before departure. The main
    controller decides whether that request can be supplied after essential
    household and heat pump demand are secured.
    """

    if not EV_connected or SOC_ev >= SOC_target_ev:
        return 0.0

    energy_needed_bus_side = ((SOC_target_ev - SOC_ev) * C_ev) / eff_ev

    if EV_remaining_time > 0:
        p_req = energy_needed_bus_side / EV_remaining_time
    else:
        p_req = P_nom_ev

    return clamp(p_req, 0.0, P_nom_ev)


def battery_discharge_limit(SOC_bss):
    """
    Compute the maximum allowed battery discharge power [kW].

    The limit is based on:
    - available energy above SOC_min_bss
    - battery discharge efficiency
    - timestep length
    - nominal battery power rating

    Battery discharge is represented later as negative P_bss.
    """
    available_energy = max(0.0, (SOC_bss - SOC_min_bss) * C_bss)
    max_power_from_soc = available_energy * eff_bss / delta_t
    return clamp(max_power_from_soc, 0.0, P_nom_bss)


def battery_charge_limit(SOC_bss):
    """
    Compute the maximum allowed battery charge power [kW].

    The limit is based on:
    - remaining capacity below SOC_max_bss
    - battery charging efficiency
    - timestep length
    - nominal battery power rating

    Battery charging is represented later as positive P_bss.
    """
    free_capacity = max(0.0, (SOC_max_bss - SOC_bss) * C_bss)
    max_power_from_soc = free_capacity / (eff_bss * delta_t)
    return clamp(max_power_from_soc, 0.0, P_nom_bss)


def ev_discharge_limit(EV_connected, SOC_ev, soc_floor=0.5):
    """
    Compute the maximum allowed EV discharge power [kW].

    This is used when the EV supports the house through V2H. The EV can only
    discharge if it is connected and its SOC is above the chosen V2H floor.
    The stricter value between SOC_min_ev and soc_floor is used.
    """

    if not EV_connected:
        return 0.0

    soc_min_supply = max(SOC_min_ev, soc_floor)
    available_energy = max(0.0, (SOC_ev - soc_min_supply) * C_ev)
    max_power_from_soc = available_energy * eff_ev / delta_t
    return clamp(max_power_from_soc, 0.0, P_nom_ev)


# =============================================================================
# RULE-BASED CONTROL DECISION
# =============================================================================

def control_decision(
    P_load, P_pv_max, EV_connected, EV_remaining_time,
    P_gen_prev, SOC_bss, SOC_ev, P_loss, T_set, T_hp, model=None
):
    """
    Main rule-based energy management controller.

    Dispatch hierarchy:
    1. Compute heat pump demand.
    2. Secure essential demand using:
       PV -> battery discharge -> EV V2H -> generator.
    3. After essential demand is secured, use surplus energy for charging:
       surplus PV -> EV first, then battery.
       generator minimum-power excess -> battery first, then EV.
    4. If no generator is running and essential demand is secured, the battery
       may support EV charging.

    Sign convention:
    - P_bss > 0: battery charging
    - P_bss < 0: battery discharging
    - P_ev  > 0: EV charging
    - P_ev  < 0: EV discharging / V2H
    """

    # -------------------------------------------------------------------------
    # 1. Heat pump and essential demand
    # -------------------------------------------------------------------------
    P_hp = hp_power(T_set, T_hp, P_loss)

    essential_demand = P_load + P_hp

    # EV charging is flexible and is not included in essential demand.
    ev_req = ev_charge_request(EV_connected, SOC_ev, EV_remaining_time)

    # Device limits for the current timestep.
    bss_dis_lim = battery_discharge_limit(SOC_bss)
    bss_ch_lim = battery_charge_limit(SOC_bss)
    ev_dis_lim = ev_discharge_limit(EV_connected, SOC_ev, soc_floor=0.5)

    # Internal split-flow variables.
    P_pv = 0.0
    P_gen = 0.0

    P_bss_charge = 0.0
    P_bss_discharge = 0.0

    P_ev_charge = 0.0
    P_ev_discharge = 0.0

    # Mode locks prevent simultaneous charge and discharge in the same timestep.
    bss_mode = "idle"   # idle / charge / discharge
    ev_mode = "idle"    # idle / charge / discharge

    # -------------------------------------------------------------------------
    # 2. Phase A: secure essential demand
    #    Priority: PV -> battery -> EV V2H -> generator
    # -------------------------------------------------------------------------
    rem_ess = essential_demand

    # A1. PV supplies essential demand first.
    pv_to_ess = min(P_pv_max, rem_ess)
    P_pv += pv_to_ess
    rem_ess -= pv_to_ess

    # Remaining PV can be used later for flexible charging.
    rem_pv = max(0.0, P_pv_max - pv_to_ess)

    # A2. Battery supplies remaining essential demand.
    if rem_ess > EPS and bss_mode == "idle":
        batt_to_ess = min(bss_dis_lim, rem_ess)
        if batt_to_ess > EPS:
            P_bss_discharge += batt_to_ess
            rem_ess -= batt_to_ess
            bss_mode = "discharge"

    # A3. EV V2H supplies remaining essential demand if needed.
    if rem_ess > EPS and ev_mode == "idle":
        ev_to_ess = min(ev_dis_lim, rem_ess)
        if ev_to_ess > EPS:
            P_ev_discharge += ev_to_ess
            rem_ess -= ev_to_ess
            ev_mode = "discharge"

    # A4. Generator supplies any remaining essential demand.
    excess_gen = 0.0
    if rem_ess > EPS:
        P_gen = clamp(rem_ess, P_min_gen, P_max_gen)

        gen_to_ess = min(P_gen, rem_ess)
        rem_ess -= gen_to_ess

        # If P_min_gen is larger than the required remaining demand,
        # the extra generator output is redirected later to charging.
        excess_gen = max(0.0, P_gen - gen_to_ess)

    # A4b. If generator minimum output creates excess, first cancel any battery
    # discharge used earlier in the same timestep.
    if excess_gen > EPS and P_bss_discharge > EPS:
        offset = min(excess_gen, P_bss_discharge)

        P_bss_discharge -= offset
        excess_gen -= offset

        if P_bss_discharge <= EPS:
            P_bss_discharge = 0.0
            bss_mode = "idle"

    # A5. Emergency heat pump shedding if essential demand is still not met.
    if rem_ess > EPS and P_hp > EPS:
        hp_shed = min(P_hp, rem_ess)
        P_hp -= hp_shed
        rem_ess -= hp_shed

    # -------------------------------------------------------------------------
    # 3. Phase B: flexible charging after essential demand is secured
    # -------------------------------------------------------------------------
    essential_secured = rem_ess <= EPS

    if essential_secured:

        # B1. Use surplus PV for EV charging first, then battery charging.
        if rem_pv > EPS:

            if ev_mode == "idle" and ev_req > EPS:
                pv_to_ev = min(rem_pv, ev_req)

                if pv_to_ev > EPS:
                    P_ev_charge += pv_to_ev
                    rem_pv -= pv_to_ev
                    ev_req -= pv_to_ev
                    P_pv += pv_to_ev
                    ev_mode = "charge"

            if rem_pv > EPS and bss_mode == "idle":
                pv_to_bss = min(rem_pv, bss_ch_lim)

                if pv_to_bss > EPS:
                    P_bss_charge += pv_to_bss
                    rem_pv -= pv_to_bss
                    bss_ch_lim -= pv_to_bss
                    P_pv += pv_to_bss
                    bss_mode = "charge"

        # B2. Use generator minimum-power excess for battery first, then EV.
        if excess_gen > EPS:

            if P_bss_discharge <= EPS:
                P_bss_discharge = 0.0
                bss_mode = "idle"

            if bss_mode == "idle":
                gen_to_bss = min(excess_gen, bss_ch_lim)

                if gen_to_bss > EPS:
                    P_bss_charge += gen_to_bss
                    excess_gen -= gen_to_bss
                    bss_ch_lim -= gen_to_bss
                    bss_mode = "charge"

            if excess_gen > EPS and ev_mode == "idle" and ev_req > EPS:
                gen_to_ev = min(excess_gen, ev_req)

                if gen_to_ev > EPS:
                    P_ev_charge += gen_to_ev
                    excess_gen -= gen_to_ev
                    ev_req -= gen_to_ev
                    ev_mode = "charge"

        # B3. Battery may support EV charging only if:
        # - essential demand is already met
        # - generator is off
        # - battery was not used for essential demand
        # - battery is not charging
        # - EV is idle and still requires charge
        if (
            ev_req > EPS
            and essential_secured
            and P_gen <= EPS
            and P_bss_discharge <= EPS
            and bss_mode == "idle"
            and ev_mode == "idle"
        ):

            batt_to_ev = min(bss_dis_lim, ev_req)

            if batt_to_ev > EPS:
                P_bss_discharge += batt_to_ev
                P_ev_charge += batt_to_ev
                ev_req -= batt_to_ev

                bss_mode = "discharge"
                ev_mode = "charge"

    # -------------------------------------------------------------------------
    # 4. Final output formatting
    # -------------------------------------------------------------------------
    # Defensive safeguard: if both charge and discharge are nonzero, cancel
    # charging and keep discharge priority.
    if P_bss_charge > EPS and P_bss_discharge > EPS:
        P_bss_charge = 0.0

    if P_ev_charge > EPS and P_ev_discharge > EPS:
        P_ev_charge = 0.0

    # Convert split flows into signed outputs expected by run.py.
    P_bss = P_bss_charge if P_bss_charge > EPS else -P_bss_discharge
    P_ev = P_ev_charge if P_ev_charge > EPS else -P_ev_discharge

    return P_pv, P_gen, P_bss, P_ev, P_hp


# =============================================================================
# RUN SIMULATION
# =============================================================================
if __name__ == '__main__':
    import os, sys
    import plotting

    sc = sys.argv[1] if len(sys.argv) > 1 else Scenario
    results = run.run_sim(control_decision, sc)
    fig_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'figures')
    plotting.plot_res(results, scenario=f'{sc}_RB', save_dir=fig_dir)
