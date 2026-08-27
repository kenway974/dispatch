"""
Tests du suivi de position — GPS, estimation à l'estime, import externe.

La note d'un coursier ne vaut que ce que vaut la position sur laquelle elle est
calculée. Ces tests vérifient les deux garanties qui rendent cette note honnête :
l'estimation n'est jamais écrite dans l'état, et la fraîcheur ne ment jamais.
"""

import importlib
from datetime import datetime, timedelta

import pytest

from app.config import POSITION_PERIMEE_MINUTES, VITESSE_MOYENNE_KMH
from app.models.coursier import AssignedOrder, Coursier, GpsPosition
from app.models.enums import PositionSource, VehicleType, VolumeType
from app.services.geo import haversine
from app.services.position import estimer_position, position_effective


PARIS_CENTRE = GpsPosition(lat=48.8566, lon=2.3522)
MONTMARTRE   = GpsPosition(lat=48.8864, lon=2.3432)   # ~3.4 km au nord
NATION       = GpsPosition(lat=48.8483, lon=2.3958)   # ~3.2 km à l'est


def coursier(
    vehicule: VehicleType = VehicleType.SCOOT_50,
    position: GpsPosition = PARIS_CENTRE,
    source: PositionSource = PositionSource.GPS,
    age_minutes: float = 0.0,
    courses: list[AssignedOrder] | None = None,
) -> Coursier:
    return Coursier(
        code="KEN",
        vehicle_type=vehicule,
        position=position,
        position_source=source,
        position_updated_at=datetime.now() - timedelta(minutes=age_minutes),
        assigned_orders=courses or [],
    )


def course(depart: GpsPosition, arrivee: GpsPosition, order_id: str = "O1") -> AssignedOrder:
    return AssignedOrder(
        order_id=order_id,
        pickup_lat=depart.lat, pickup_lon=depart.lon,
        delivery_lat=arrivee.lat, delivery_lon=arrivee.lon,
        volume_type=VolumeType.STANDARD,
    )


class TestGpsFrais:

    def test_ping_recent_est_temps_reel(self) -> None:
        estimation = estimer_position(coursier(age_minutes=0.3))
        assert estimation.source == "gps"
        assert estimation.temps_reel is True
        assert estimation.perimee is False
        assert estimation.distance_parcourue_km == 0.0

    def test_position_gps_fraiche_nest_pas_projetee(self) -> None:
        """Un point GPS de trente secondes se suffit : rien à extrapoler."""
        c = coursier(age_minutes=0.5, courses=[course(MONTMARTRE, NATION)])
        estimation = estimer_position(c)
        assert estimation.position.lat == pytest.approx(PARIS_CENTRE.lat)
        assert estimation.explication.startswith("Position GPS")


class TestEstimation:

    def test_sans_course_le_coursier_reste_sur_place(self) -> None:
        """Un coursier en attente ne se déplace pas : rien à projeter."""
        estimation = estimer_position(coursier(age_minutes=15))
        assert estimation.position.lat == pytest.approx(PARIS_CENTRE.lat)
        assert estimation.distance_parcourue_km == 0.0
        assert "sans course en cours" in estimation.explication

    def test_avec_course_la_position_avance_vers_le_ramassage(self) -> None:
        c = coursier(age_minutes=6, courses=[course(MONTMARTRE, NATION)])
        estimation = estimer_position(c)

        assert estimation.source == "estimee"
        assert estimation.distance_parcourue_km > 0
        # Il s'est rapproché de son point de ramassage
        depart = haversine(PARIS_CENTRE, MONTMARTRE)
        arrive = haversine(estimation.position, MONTMARTRE)
        assert arrive < depart

    def test_la_distance_projetee_suit_la_vitesse_du_vehicule(self) -> None:
        """Six minutes à 18 km/h font 1,8 km — la projection doit s'y tenir."""
        c = coursier(age_minutes=6, courses=[course(MONTMARTRE, NATION)])
        attendu = VITESSE_MOYENNE_KMH[VehicleType.SCOOT_50] * (6 / 60)
        assert estimer_position(c).distance_parcourue_km == pytest.approx(attendu, rel=0.02)

    def test_un_fourgon_avance_moins_vite_qu_un_scooter(self) -> None:
        trajet = [course(MONTMARTRE, NATION)]
        scooter = estimer_position(coursier(VehicleType.SCOOT_50, age_minutes=8, courses=trajet))
        fourgon = estimer_position(coursier(VehicleType.FOURGON, age_minutes=8, courses=trajet))
        assert fourgon.distance_parcourue_km < scooter.distance_parcourue_km

    def test_le_coursier_ne_depasse_jamais_la_fin_de_sa_tournee(self) -> None:
        """Tournée terminée : il attend au dernier point, il ne part pas à l'infini."""
        c = coursier(age_minutes=600, courses=[course(MONTMARTRE, NATION)])
        estimation = estimer_position(c)
        assert estimation.position.lat == pytest.approx(NATION.lat, abs=1e-6)
        assert estimation.position.lon == pytest.approx(NATION.lon, abs=1e-6)

    def test_progression_monotone_dans_le_temps(self) -> None:
        trajet = [course(MONTMARTRE, NATION)]
        distances = [
            estimer_position(coursier(age_minutes=m, courses=trajet)).distance_parcourue_km
            for m in (3, 6, 12)
        ]
        assert distances[0] < distances[1] < distances[2]

    def test_deplacement_negligeable_non_signale_comme_estimation(self) -> None:
        """Sous 50 m, annoncer une estimation ferait du bruit pour rien."""
        estimation = estimer_position(coursier(age_minutes=0.05, courses=[course(MONTMARTRE, NATION)]))
        assert estimation.source != "estimee"


class TestFraicheur:

    def test_position_recente_non_perimee(self) -> None:
        assert estimer_position(coursier(age_minutes=2)).perimee is False

    def test_position_ancienne_signalee_perimee(self) -> None:
        vieille = estimer_position(coursier(age_minutes=POSITION_PERIMEE_MINUTES + 5))
        assert vieille.perimee is True

    def test_une_position_perimee_reste_fournie(self) -> None:
        """Mieux vaut une position douteuse et signalée qu'un trou dans le tableau."""
        estimation = estimer_position(coursier(age_minutes=180))
        assert estimation.position is not None
        assert estimation.perimee is True

    def test_saisie_manuelle_jamais_temps_reel(self) -> None:
        """Une adresse tapée n'est pas un signal GPS, même saisie à l'instant."""
        estimation = estimer_position(coursier(source=PositionSource.MANUELLE, age_minutes=0))
        assert estimation.temps_reel is False
        assert estimation.source == "manuelle"

    def test_age_formate_lisiblement(self) -> None:
        assert "s" in estimer_position(coursier(age_minutes=0.5)).explication
        assert "min" in estimer_position(coursier(age_minutes=8)).explication
        assert "h" in estimer_position(coursier(age_minutes=150)).explication


class TestEstimationNonPersistee:
    """La garantie centrale : une estimation ne doit jamais devenir un fait."""

    def test_estimer_ne_modifie_pas_le_coursier(self) -> None:
        c = coursier(age_minutes=10, courses=[course(MONTMARTRE, NATION)])
        avant = (c.position.lat, c.position.lon, c.position_updated_at)
        estimer_position(c)
        assert (c.position.lat, c.position.lon, c.position_updated_at) == avant

    def test_estimations_repetees_ne_derivent_pas(self) -> None:
        """Estimer dix fois de suite doit donner dix fois le même point."""
        c = coursier(age_minutes=10, courses=[course(MONTMARTRE, NATION)])
        instant = datetime.now()
        points = {
            (round(estimer_position(c, instant).position.lat, 9),
             round(estimer_position(c, instant).position.lon, 9))
            for _ in range(10)
        }
        assert len(points) == 1

    def test_position_effective_alimente_le_scoring(self) -> None:
        c = coursier(age_minutes=10, courses=[course(MONTMARTRE, NATION)])
        instant = datetime.now()   # même instant des deux côtés : la position bouge avec le temps
        assert position_effective(c, instant) == estimer_position(c, instant).position


# ---------------------------------------------------------------------------
# Import depuis le système de suivi de l'entreprise
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPATCH_DB_PATH", str(tmp_path / "pos.db"))
    monkeypatch.setenv("DISPATCH_IMPORT_TOKEN", "jeton-de-test")
    for nom in ("app.services.storage", "app.services.comparaison",
                "app.api.routes_pilote", "app.main"):
        importlib.reload(importlib.import_module(nom))

    import app.main as main_module
    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        c.post("/demo/reset")
        c.post("/coursiers", json={"code": "KEN", "vehicle_type": "scoot_50",
                                   "lat": 48.8566, "lon": 2.3522})
        yield c


class TestImportPositions:

    def test_import_met_a_jour_la_position(self, client) -> None:
        r = client.post("/positions/import",
                        headers={"X-Import-Token": "jeton-de-test"},
                        json={"positions": [{"code": "KEN", "lat": 48.8864, "lon": 2.3432}]})
        assert r.status_code == 200
        assert r.json()["mises_a_jour"] == 1

        coursier_maj = client.get("/coursiers").json()[0]
        assert coursier_maj["lat"] == pytest.approx(48.8864)
        assert coursier_maj["position"]["source"] == "import"

    def test_horodatage_source_respecte(self, client) -> None:
        """Une position relevée il y a douze minutes ne doit pas passer pour fraîche."""
        mesure = (datetime.now() - timedelta(minutes=12)).isoformat()
        client.post("/positions/import",
                    headers={"X-Import-Token": "jeton-de-test"},
                    json={"positions": [{"code": "KEN", "lat": 48.8864, "lon": 2.3432,
                                         "horodatage": mesure}]})
        position = client.get("/coursiers").json()[0]["position"]
        assert position["age_secondes"] > 600

    def test_codes_inconnus_signales_sans_bloquer_le_lot(self, client) -> None:
        """Le système source suit toute la flotte, l'essai n'en couvre qu'une partie."""
        r = client.post("/positions/import",
                        headers={"X-Import-Token": "jeton-de-test"},
                        json={"positions": [
                            {"code": "KEN", "lat": 48.88, "lon": 2.34},
                            {"code": "ZZZ", "lat": 48.90, "lon": 2.30},
                        ]})
        assert r.status_code == 200
        assert r.json()["mises_a_jour"] == 1
        assert r.json()["codes_inconnus"] == ["ZZZ"]

    def test_jeton_absent_refuse(self, client) -> None:
        r = client.post("/positions/import",
                        json={"positions": [{"code": "KEN", "lat": 48.88, "lon": 2.34}]})
        assert r.status_code == 401

    def test_jeton_invalide_refuse(self, client) -> None:
        r = client.post("/positions/import",
                        headers={"X-Import-Token": "mauvais"},
                        json={"positions": [{"code": "KEN", "lat": 48.88, "lon": 2.34}]})
        assert r.status_code == 401

    def test_import_desactive_si_aucun_jeton_configure(self, client, monkeypatch) -> None:
        """Fermé par défaut : sans jeton configuré, l'endpoint refuse au lieu de s'ouvrir."""
        monkeypatch.delenv("DISPATCH_IMPORT_TOKEN", raising=False)
        r = client.post("/positions/import",
                        headers={"X-Import-Token": "jeton-de-test"},
                        json={"positions": [{"code": "KEN", "lat": 48.88, "lon": 2.34}]})
        assert r.status_code == 503

    def test_endpoint_positions_expose_la_fraicheur(self, client) -> None:
        coursiers = client.get("/pilote/positions").json()["coursiers"]
        assert coursiers[0]["code"] == "KEN"
        assert "age_secondes" in coursiers[0] and "explication" in coursiers[0]

    def test_ping_telephone_marque_la_source_gps(self, client) -> None:
        r = client.post("/coursiers/KEN/ping", json={"lat": 48.8864, "lon": 2.3432, "precision_m": 12})
        assert r.status_code == 200
        assert client.get("/coursiers").json()[0]["position"]["source"] == "gps"

    def test_ping_code_inconnu(self, client) -> None:
        assert client.post("/coursiers/ZZZ/ping", json={"lat": 48.88, "lon": 2.34}).status_code == 404
