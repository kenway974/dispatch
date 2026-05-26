"""
Suite de tests pour le moteur de dispatch.

Couvre :
  - Filtrage de l'éligibilité (zone, volume, capacité)
  - Algorithme de scoring (distance, charge, groupage)
  - Attribution correcte via dispatch_order
  - Cas limites : flotte vide, coursier plein, colis Voiture

Lance avec :
    pytest tests/test_dispatch.py -v
"""

import pytest

from app.config import GROUPAGE_PROXIMITY_KM, MAX_LOAD_BY_VEHICLE, VOLUME_WEIGHTS
from app.models.courier import AssignedOrder, Courier, GpsPosition
from app.models.enums import OrderStatus, VehicleType, VolumeType, Zone
from app.models.order import Coordinates, Order
from app.services.dispatch import (
    dispatch_order,
    get_eligible_couriers,
    is_courier_eligible,
    score_courier,
)
from app.services.fleet import FleetManager
from app.services.geo import haversine, min_distance_to_route


# ---------------------------------------------------------------------------
# Fixtures — positions GPS de référence
# ---------------------------------------------------------------------------

PARIS_CENTRE = GpsPosition(lat=48.8566, lon=2.3522)       # Île de la Cité
MONTMARTRE   = GpsPosition(lat=48.8864, lon=2.3432)       # ~3.4 km au nord
BASTILLE     = GpsPosition(lat=48.8533, lon=2.3692)       # ~1.6 km à l'est
SAINT_DENIS  = GpsPosition(lat=48.9360, lon=2.3553)       # Petite Couronne nord
VERSAILLES   = GpsPosition(lat=48.8045, lon=2.1200)       # Grande Couronne ouest
CRETEIL      = GpsPosition(lat=48.7773, lon=2.4555)       # Grande Couronne sud-est


def make_courier(
    code: str,
    vehicle_type: VehicleType,
    position: GpsPosition,
    assigned: list[AssignedOrder] | None = None,
) -> Courier:
    """Crée un coursier de test."""
    return Courier(
        code=code,
        vehicle_type=vehicle_type,
        position=position,
        assigned_orders=assigned or [],
        is_active=True,
    )


def make_order(
    order_id: str,
    zone: Zone,
    volume_type: VolumeType,
    pickup: GpsPosition = PARIS_CENTRE,
    delivery: GpsPosition = MONTMARTRE,
) -> Order:
    """Crée une commande de test."""
    return Order(
        id=order_id,
        pickup=Coordinates(lat=pickup.lat, lon=pickup.lon),
        delivery=Coordinates(lat=delivery.lat, lon=delivery.lon),
        zone=zone,
        volume_type=volume_type,
    )


def make_assigned_order(
    order_id: str,
    volume_type: VolumeType = VolumeType.STANDARD,
    pickup: GpsPosition = PARIS_CENTRE,
    delivery: GpsPosition = MONTMARTRE,
) -> AssignedOrder:
    """Crée un AssignedOrder pour remplir le portefeuille d'un coursier."""
    return AssignedOrder(
        order_id=order_id,
        pickup_lat=pickup.lat,
        pickup_lon=pickup.lon,
        delivery_lat=delivery.lat,
        delivery_lon=delivery.lon,
        volume_type=volume_type,
    )


# ---------------------------------------------------------------------------
# Tests géographiques
# ---------------------------------------------------------------------------

class TestGeo:

    def test_haversine_same_point(self) -> None:
        """Distance entre deux points identiques doit être 0."""
        assert haversine(PARIS_CENTRE, PARIS_CENTRE) == pytest.approx(0.0, abs=1e-6)

    def test_haversine_paris_montmartre(self) -> None:
        """Vérifie une distance connue : Paris centre → Montmartre ≈ 3.4 km."""
        dist = haversine(PARIS_CENTRE, MONTMARTRE)
        assert 3.0 < dist < 4.0, f"Distance inattendue : {dist:.2f} km"

    def test_haversine_symmetry(self) -> None:
        """La distance A→B doit être égale à B→A."""
        d1 = haversine(PARIS_CENTRE, VERSAILLES)
        d2 = haversine(VERSAILLES, PARIS_CENTRE)
        assert d1 == pytest.approx(d2, rel=1e-6)

    def test_min_distance_to_route_nearby(self) -> None:
        """Un coursier à Montmartre avec waypoints parisiens est proche de Bastille."""
        courier = make_courier(
            "KEN", VehicleType.SCOOT_VILLE, MONTMARTRE,
            assigned=[make_assigned_order("O1", pickup=PARIS_CENTRE, delivery=BASTILLE)],
        )
        target = GpsPosition(lat=48.8540, lon=2.3700)  # très près de Bastille
        dist = min_distance_to_route(courier, target)
        assert dist < GROUPAGE_PROXIMITY_KM


# ---------------------------------------------------------------------------
# Tests d'éligibilité
# ---------------------------------------------------------------------------

class TestEligibility:

    def test_scoot_ville_eligible_for_paris(self) -> None:
        courier = make_courier("KEN", VehicleType.SCOOT_VILLE, PARIS_CENTRE)
        order = make_order("O1", Zone.PARIS, VolumeType.STANDARD)
        assert is_courier_eligible(courier, order) is True

    def test_scoot_ville_not_eligible_for_petite_couronne(self) -> None:
        courier = make_courier("KEN", VehicleType.SCOOT_VILLE, PARIS_CENTRE)
        order = make_order("O1", Zone.PETITE_COURONNE, VolumeType.STANDARD)
        assert is_courier_eligible(courier, order) is False

    def test_scoot_banlieue_proche_not_eligible_for_paris(self) -> None:
        courier = make_courier("MAR", VehicleType.SCOOT_BANLIEUE_PROCHE, SAINT_DENIS)
        order = make_order("O1", Zone.PARIS, VolumeType.STANDARD)
        assert is_courier_eligible(courier, order) is False

    def test_fourgon_eligible_for_grande_couronne(self) -> None:
        courier = make_courier("FOU", VehicleType.FOURGON, VERSAILLES)
        order = make_order("O1", Zone.GRANDE_COURONNE, VolumeType.STANDARD)
        assert is_courier_eligible(courier, order) is True

    def test_fourgon_eligible_for_voiture_any_zone(self) -> None:
        """Le fourgon peut prendre un colis Voiture même en zone Paris."""
        courier = make_courier("FOU", VehicleType.FOURGON, PARIS_CENTRE)
        order = make_order("O1", Zone.PARIS, VolumeType.VOITURE)
        assert is_courier_eligible(courier, order) is True

    def test_scoot_cannot_take_voiture(self) -> None:
        """Aucun scooter ne peut transporter un colis Voiture."""
        for vtype in [VehicleType.SCOOT_VILLE, VehicleType.SCOOT_BANLIEUE_PROCHE, VehicleType.SCOOT_BANLIEUE_LOIN]:
            courier = make_courier("KEN", vtype, PARIS_CENTRE)
            order = make_order("O1", Zone.PARIS, VolumeType.VOITURE)
            assert is_courier_eligible(courier, order) is False, f"Scoot {vtype} ne devrait pas accepter Voiture"

    def test_inactive_courier_not_eligible(self) -> None:
        courier = make_courier("KEN", VehicleType.SCOOT_VILLE, PARIS_CENTRE)
        courier.is_active = False
        order = make_order("O1", Zone.PARIS, VolumeType.STANDARD)
        assert is_courier_eligible(courier, order) is False

    def test_courier_at_capacity_not_eligible(self) -> None:
        """Un scoot plein (5/5 unités Standard) ne doit pas être éligible."""
        assigned = [make_assigned_order(f"O{i}", VolumeType.STANDARD) for i in range(5)]
        courier = make_courier("KEN", VehicleType.SCOOT_VILLE, PARIS_CENTRE, assigned=assigned)
        assert courier.current_load == 5
        assert courier.is_at_capacity is True
        order = make_order("ONEW", Zone.PARIS, VolumeType.STANDARD)
        assert is_courier_eligible(courier, order) is False

    def test_courier_partial_load_still_eligible(self) -> None:
        """Un scoot avec 3/5 unités peut encore prendre un Standard (1 unité)."""
        assigned = [make_assigned_order(f"O{i}", VolumeType.STANDARD) for i in range(3)]
        courier = make_courier("KEN", VehicleType.SCOOT_VILLE, PARIS_CENTRE, assigned=assigned)
        order = make_order("ONEW", Zone.PARIS, VolumeType.STANDARD)
        assert is_courier_eligible(courier, order) is True

    def test_volume_colis_fits_remaining_capacity(self) -> None:
        """Un scoot avec 4 unités utilisées ne peut plus prendre un Volume (2 unités)."""
        assigned = [
            make_assigned_order("O1", VolumeType.VOLUME),    # 2 unités
            make_assigned_order("O2", VolumeType.STANDARD),  # 1 unité
            make_assigned_order("O3", VolumeType.STANDARD),  # 1 unité → total 4
        ]
        courier = make_courier("KEN", VehicleType.SCOOT_VILLE, PARIS_CENTRE, assigned=assigned)
        assert courier.current_load == 4
        order = make_order("ONEW", Zone.PARIS, VolumeType.VOLUME)  # besoin de 2 unités
        assert is_courier_eligible(courier, order) is False


# ---------------------------------------------------------------------------
# Tests de scoring
# ---------------------------------------------------------------------------

class TestScoring:

    def test_closer_courier_has_lower_score(self) -> None:
        """Le coursier plus proche du ramassage doit avoir un meilleur score."""
        order = make_order("O1", Zone.PARIS, VolumeType.STANDARD, pickup=PARIS_CENTRE)

        near_courier = make_courier("NEA", VehicleType.SCOOT_VILLE, BASTILLE)    # ~1.6 km
        far_courier  = make_courier("FAR", VehicleType.SCOOT_VILLE, MONTMARTRE)  # ~3.4 km

        score_near = score_courier(near_courier, order)
        score_far  = score_courier(far_courier, order)

        assert score_near < score_far, (
            f"Proche devrait scorer moins : {score_near:.2f} < {score_far:.2f}"
        )

    def test_loaded_courier_penalized(self) -> None:
        """
        Deux coursiers à la même distance : celui avec plus de charge
        doit avoir un score plus élevé (moins prioritaire).
        """
        order = make_order("O1", Zone.PARIS, VolumeType.STANDARD, pickup=PARIS_CENTRE)

        light = make_courier("LGT", VehicleType.SCOOT_VILLE, BASTILLE)
        heavy = make_courier(
            "HVY", VehicleType.SCOOT_VILLE, BASTILLE,
            assigned=[make_assigned_order(f"O{i}") for i in range(3)],
        )

        assert score_courier(light, order) < score_courier(heavy, order)

    def test_groupage_reduces_score(self) -> None:
        """
        Un coursier dont le trajet passe déjà près du ramassage doit avoir
        un score inférieur à un coursier plus proche en position mais sans groupage.
        """
        # Ramassage très proche de Bastille
        pickup_near_bastille = GpsPosition(lat=48.8540, lon=48.3710)
        order = make_order("O1", Zone.PARIS, VolumeType.STANDARD,
                           pickup=GpsPosition(lat=48.8530, lon=2.3690))

        # Coursier A : position à Paris centre, trajet passe PAR Bastille (groupage possible)
        courier_with_groupage = make_courier(
            "GRP", VehicleType.SCOOT_VILLE, PARIS_CENTRE,
            assigned=[make_assigned_order("OX", pickup=BASTILLE, delivery=MONTMARTRE)],
        )

        # Coursier B : même distance, sans courses
        courier_no_groupage = make_courier("NGP", VehicleType.SCOOT_VILLE, PARIS_CENTRE)

        score_grp = score_courier(courier_with_groupage, order)
        score_ngp = score_courier(courier_no_groupage, order)

        # Le coursier avec groupage passe près du ramassage → doit être favorisé
        assert score_grp < score_ngp


# ---------------------------------------------------------------------------
# Tests du dispatch complet
# ---------------------------------------------------------------------------

class TestDispatch:

    def _make_fleet(self) -> FleetManager:
        """Crée une flotte de test isolée (non partagée avec l'API)."""
        fleet = FleetManager()
        fleet.add_courier(make_courier("KEN", VehicleType.SCOOT_VILLE, PARIS_CENTRE))
        fleet.add_courier(make_courier("THO", VehicleType.SCOOT_VILLE, MONTMARTRE))
        fleet.add_courier(make_courier("MAR", VehicleType.SCOOT_BANLIEUE_PROCHE, SAINT_DENIS))
        fleet.add_courier(make_courier("FOU", VehicleType.FOURGON, VERSAILLES))
        return fleet

    def test_dispatch_assigns_nearest_courier(self) -> None:
        """La commande Paris Standard doit aller au coursier Paris le plus proche."""
        fleet = self._make_fleet()
        # Ramassage à Bastille (~1.6 km de KEN, ~5 km de THO)
        order = make_order("O1", Zone.PARIS, VolumeType.STANDARD, pickup=BASTILLE)
        fleet.add_order(order)

        result = dispatch_order(order, fleet)

        assert result.success is True
        assert result.assigned_to == "KEN"  # plus proche de Bastille
        assert order.status == OrderStatus.ASSIGNED

    def test_dispatch_petite_couronne_goes_to_right_vehicle(self) -> None:
        """Une commande Petite Couronne doit aller à scoot_banlieue_proche, pas scoot_ville."""
        fleet = self._make_fleet()
        order = make_order("O2", Zone.PETITE_COURONNE, VolumeType.STANDARD, pickup=SAINT_DENIS)
        fleet.add_order(order)

        result = dispatch_order(order, fleet)

        assert result.success is True
        assert result.assigned_to == "MAR"

    def test_dispatch_voiture_goes_to_fourgon(self) -> None:
        """Un colis Voiture doit obligatoirement aller au fourgon."""
        fleet = self._make_fleet()
        order = make_order("O3", Zone.GRANDE_COURONNE, VolumeType.VOITURE, pickup=VERSAILLES)
        fleet.add_order(order)

        result = dispatch_order(order, fleet)

        assert result.success is True
        assert result.assigned_to == "FOU"

    def test_dispatch_no_eligible_courier(self) -> None:
        """Si aucun coursier éligible, le statut devient UNASSIGNABLE."""
        fleet = FleetManager()  # flotte vide
        order = make_order("O4", Zone.PARIS, VolumeType.STANDARD)
        fleet.add_order(order)

        result = dispatch_order(order, fleet)

        assert result.success is False
        assert result.assigned_to is None
        assert order.status == OrderStatus.UNASSIGNABLE

    def test_dispatch_updates_courier_load(self) -> None:
        """Après attribution, la charge du coursier doit être mise à jour."""
        fleet = self._make_fleet()
        courier = fleet.get_courier("KEN")
        assert courier is not None
        initial_load = courier.current_load

        order = make_order("O5", Zone.PARIS, VolumeType.STANDARD)
        fleet.add_order(order)
        result = dispatch_order(order, fleet)

        assert result.success is True
        new_load = courier.current_load
        expected_gain = VOLUME_WEIGHTS[VolumeType.STANDARD]
        assert new_load == initial_load + expected_gain

    def test_dispatch_full_courier_skipped(self) -> None:
        """Un coursier plein ne doit pas être choisi même s'il est le plus proche."""
        fleet = FleetManager()
        # Coursier plein (5/5 Standard)
        full = make_courier(
            "FUL", VehicleType.SCOOT_VILLE, PARIS_CENTRE,
            assigned=[make_assigned_order(f"O{i}") for i in range(5)],
        )
        # Coursier libre mais plus loin
        free = make_courier("FRE", VehicleType.SCOOT_VILLE, MONTMARTRE)
        fleet.add_courier(full)
        fleet.add_courier(free)

        order = make_order("ONEW", Zone.PARIS, VolumeType.STANDARD, pickup=PARIS_CENTRE)
        fleet.add_order(order)
        result = dispatch_order(order, fleet)

        assert result.success is True
        assert result.assigned_to == "FRE"  # FUL est plein, FRE est le seul dispo

    def test_dispatch_multiple_orders_sequential(self) -> None:
        """Deux commandes successives doivent toutes deux être assignées."""
        fleet = self._make_fleet()

        o1 = make_order("O6", Zone.PARIS, VolumeType.STANDARD)
        o2 = make_order("O7", Zone.PARIS, VolumeType.STANDARD)
        fleet.add_order(o1)
        fleet.add_order(o2)

        r1 = dispatch_order(o1, fleet)
        r2 = dispatch_order(o2, fleet)

        assert r1.success is True
        assert r2.success is True
        assert o1.status == OrderStatus.ASSIGNED
        assert o2.status == OrderStatus.ASSIGNED
