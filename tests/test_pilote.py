"""
Tests du mode pilote — comparaison entre dispatch manuel et dispatch automatique.

Couvre :
  - Classement : ordre des éligibles, motifs des écartés
  - Comparaison : accord, désaccord, rang du choix manuel, écart de score
  - Règle structurante : la flotte suit le choix HUMAIN, jamais celui du moteur
  - Journal : persistance, statistiques, suppression
  - Endpoints HTTP du mode pilote

Chaque test s'exécute sur une base SQLite temporaire isolée.
"""

import importlib

import pytest

from app.models.coursier import Coursier, GpsPosition
from app.models.enums import VehicleType, VolumeType, Zone
from app.models.order import Coordinates, Order
from app.services.fleet import FleetManager


PARIS_CENTRE = GpsPosition(lat=48.8566, lon=2.3522)
MONTMARTRE   = GpsPosition(lat=48.8864, lon=2.3432)
BASTILLE     = GpsPosition(lat=48.8533, lon=2.3692)
VERSAILLES   = GpsPosition(lat=48.8045, lon=2.1200)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Recharge le module de persistance sur une base temporaire, vierge par test."""
    monkeypatch.setenv("DISPATCH_DB_PATH", str(tmp_path / "pilote.db"))
    import app.services.storage as module
    importlib.reload(module)
    module.init_db()
    # comparaison capture `storage` à l'import : on le recharge pour qu'il voie la nouvelle base
    import app.services.comparaison as comparaison
    importlib.reload(comparaison)
    return module


@pytest.fixture
def comparaison(storage):
    """Le module de comparaison, lié à la base temporaire du test."""
    import app.services.comparaison as module
    return module


def make_coursier(code: str, vehicle_type: VehicleType, position: GpsPosition) -> Coursier:
    return Coursier(code=code, vehicle_type=vehicle_type, position=position)


def make_order(
    order_id: str = "ORD-1",
    zone: Zone = Zone.PARIS,
    volume: VolumeType = VolumeType.STANDARD,
    pickup: GpsPosition = PARIS_CENTRE,
) -> Order:
    return Order(
        id=order_id,
        pickup=Coordinates(lat=pickup.lat, lon=pickup.lon),
        delivery=Coordinates(lat=MONTMARTRE.lat, lon=MONTMARTRE.lon),
        zone=zone,
        volume_type=volume,
    )


@pytest.fixture
def flotte() -> FleetManager:
    """Trois coursiers : un tout proche, un plus loin, un hors zone."""
    fleet = FleetManager()
    fleet.add_coursier(make_coursier("KEN", VehicleType.SCOOT_VILLE, PARIS_CENTRE))
    fleet.add_coursier(make_coursier("MEH", VehicleType.SCOOT_VILLE, MONTMARTRE))
    fleet.add_coursier(make_coursier("LAH", VehicleType.SCOOT_BANLIEUE_LOIN, VERSAILLES))
    return fleet


# ---------------------------------------------------------------------------
# Classement
# ---------------------------------------------------------------------------

class TestClassement:

    def test_eligibles_tries_du_meilleur_au_pire(self, comparaison, flotte) -> None:
        classement = comparaison.classer_coursiers(make_order(), flotte)
        eligibles = [c for c in classement if c.eligible]
        assert [c.code for c in eligibles] == ["KEN", "MEH"]
        assert [c.rang for c in eligibles] == [1, 2]
        assert eligibles[0].score < eligibles[1].score

    def test_ecartes_portent_leur_motif(self, comparaison, flotte) -> None:
        """LAH ne couvre pas Paris : il doit apparaître, écarté, avec la raison."""
        classement = comparaison.classer_coursiers(make_order(), flotte)
        lah = next(c for c in classement if c.code == "LAH")
        assert lah.eligible is False
        assert lah.rang is None
        assert "hors périmètre" in lah.motif_inegibilite

    def test_toute_la_flotte_est_representee(self, comparaison, flotte) -> None:
        classement = comparaison.classer_coursiers(make_order(), flotte)
        assert {c.code for c in classement} == {"KEN", "MEH", "LAH"}

    def test_decomposition_du_score_disponible(self, comparaison, flotte) -> None:
        """Le dispatcheur doit pouvoir lire d'où vient le score, pas seulement le total."""
        classement = comparaison.classer_coursiers(make_order(), flotte)
        ken = next(c for c in classement if c.code == "KEN")
        assert ken.distance_km is not None
        assert ken.explications and "ramassage" in ken.explications[0]


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------

class TestComparaison:

    def test_accord_quand_meme_choix(self, comparaison, flotte) -> None:
        res = comparaison.comparer(make_order(), flotte, choix_manuel="KEN")
        assert res.accord is True
        assert res.choix_app == "KEN"
        assert res.rang_manuel == 1
        assert "Accord" in res.verdict

    def test_desaccord_donne_rang_et_ecart(self, comparaison, flotte) -> None:
        res = comparaison.comparer(make_order(), flotte, choix_manuel="MEH")
        assert res.accord is False
        assert res.choix_app == "KEN"
        assert res.rang_manuel == 2
        assert res.ecart_km > 0          # le choix manuel coûte plus cher
        assert "Désaccord" in res.verdict

    def test_choix_manuel_inegible_explique_pourquoi(self, comparaison, flotte) -> None:
        res = comparaison.comparer(make_order(), flotte, choix_manuel="LAH")
        assert res.accord is False
        assert res.rang_manuel is None
        assert "hors périmètre" in res.verdict

    def test_code_manuel_insensible_a_la_casse(self, comparaison, flotte) -> None:
        res = comparaison.comparer(make_order(), flotte, choix_manuel="ken")
        assert res.choix_manuel == "KEN"
        assert res.accord is True

    def test_sans_choix_manuel_pas_d_accord(self, comparaison, flotte) -> None:
        res = comparaison.comparer(make_order(), flotte, choix_manuel=None)
        assert res.accord is False
        assert res.choix_app == "KEN"
        assert "aurait choisi" in res.verdict

    def test_aucun_eligible(self, comparaison) -> None:
        fleet = FleetManager()
        fleet.add_coursier(make_coursier("KEN", VehicleType.SCOOT_VILLE, PARIS_CENTRE))
        res = comparaison.comparer(make_order(volume=VolumeType.VOITURE), fleet, choix_manuel="KEN")
        assert res.choix_app is None
        assert res.accord is False


class TestFlotteSuitLeTerrain:
    """La règle qui rend l'essai exploitable : l'état simulé suit le choix humain."""

    def test_la_course_va_au_choix_manuel_pas_au_choix_app(self, comparaison, flotte) -> None:
        comparaison.comparer(make_order("ORD-1"), flotte, choix_manuel="MEH")
        assert flotte.get_coursier("MEH").order_count == 1
        assert flotte.get_coursier("KEN").order_count == 0   # le moteur voulait KEN, sans effet

    def test_les_charges_s_accumulent_entre_comparaisons(self, comparaison, flotte) -> None:
        for i in range(3):
            comparaison.comparer(make_order(f"ORD-{i}"), flotte, choix_manuel="MEH")
        assert flotte.get_coursier("MEH").current_load == 3

    def test_simulation_ne_touche_a_rien(self, comparaison, flotte, storage) -> None:
        comparaison.comparer(make_order(), flotte, choix_manuel="MEH", journaliser=False)
        assert flotte.get_coursier("MEH").order_count == 0
        assert storage.statistiques()["total"] == 0


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

class TestJournal:

    def test_chaque_comparaison_est_journalisee(self, comparaison, flotte, storage) -> None:
        comparaison.comparer(make_order("ORD-1"), flotte, choix_manuel="KEN")
        comparaison.comparer(make_order("ORD-2"), flotte, choix_manuel="MEH")
        entrees = storage.lister_comparaisons()
        assert len(entrees) == 2
        assert entrees[0]["order_id"] == "ORD-2"      # plus récente en tête

    def test_le_classement_complet_est_conserve(self, comparaison, flotte, storage) -> None:
        """Un désaccord doit rester analysable des semaines plus tard."""
        comparaison.comparer(make_order(), flotte, choix_manuel="MEH")
        entree = storage.lister_comparaisons()[0]
        assert len(entree["classement"]) == 3
        assert entree["classement"][0]["code"] == "KEN"

    def test_commentaire_conserve(self, comparaison, flotte, storage) -> None:
        comparaison.comparer(make_order(), flotte, choix_manuel="MEH", commentaire="il rentrait au dépôt")
        assert storage.lister_comparaisons()[0]["commentaire"] == "il rentrait au dépôt"

    def test_statistiques_taux_accord(self, comparaison, flotte, storage) -> None:
        comparaison.comparer(make_order("O1"), flotte, choix_manuel="KEN")   # accord
        comparaison.comparer(make_order("O2"), flotte, choix_manuel="MEH")   # désaccord
        stats = storage.statistiques()
        assert stats["total"] == 2
        assert stats["accords"] == 1
        assert stats["taux_accord"] == 50.0

    def test_statistiques_vides_sans_division_par_zero(self, storage) -> None:
        stats = storage.statistiques()
        assert stats["total"] == 0 and stats["taux_accord"] is None

    def test_repartition_par_coursier(self, comparaison, flotte, storage) -> None:
        comparaison.comparer(make_order("O1"), flotte, choix_manuel="MEH")
        par = {p["code"]: p for p in storage.statistiques()["par_coursier"]}
        assert par["MEH"]["manuel"] == 1 and par["MEH"]["app"] == 0
        assert par["KEN"]["app"] == 1

    def test_suppression_d_une_saisie(self, comparaison, flotte, storage) -> None:
        res = comparaison.comparer(make_order(), flotte, choix_manuel="KEN")
        assert storage.supprimer_comparaison(res.journal_id) is True
        assert storage.statistiques()["total"] == 0

    def test_suppression_inexistante(self, storage) -> None:
        assert storage.supprimer_comparaison(999) is False


class TestPersistanceFlotte:

    def test_instantane_puis_restauration(self, storage, flotte) -> None:
        """Un redémarrage ne doit pas effacer la flotte ni les courses en cours."""
        order = make_order("ORD-1")
        flotte.add_order(order)
        flotte.assign_order_to_coursier(order, "KEN")

        storage.sauver_etat_flotte(flotte.to_snapshot())

        restauree = FleetManager()
        restauree.restore(storage.charger_etat_flotte())

        assert {c.code for c in restauree.list_coursiers()} == {"KEN", "MEH", "LAH"}
        assert restauree.get_coursier("KEN").order_count == 1
        assert restauree.get_coursier("KEN").position.lat == pytest.approx(PARIS_CENTRE.lat)

    def test_entree_corrompue_ignoree_sans_tout_perdre(self, storage, flotte) -> None:
        instantane = flotte.to_snapshot()
        instantane["coursiers"].append({"code": "???", "vehicle_type": "inexistant"})

        restauree = FleetManager()
        restauree.restore(instantane)
        assert restauree.coursier_count == 3   # les trois valides sont là, l'invalide est ignorée

    def test_callback_declenche_sur_mutation(self, flotte) -> None:
        appels = []
        flotte.set_on_change(lambda f: appels.append(f.coursier_count))
        flotte.add_coursier(make_coursier("NEW", VehicleType.SCOOT_VILLE, BASTILLE))
        assert appels == [4]

    def test_panne_de_sauvegarde_ne_bloque_pas_le_dispatch(self, flotte) -> None:
        """Un disque plein ne doit pas empêcher d'attribuer une course."""
        def casse(_):
            raise OSError("disque plein")
        flotte.set_on_change(casse)
        flotte.add_coursier(make_coursier("NEW", VehicleType.SCOOT_VILLE, BASTILLE))
        assert flotte.get_coursier("NEW") is not None


# ---------------------------------------------------------------------------
# Endpoints HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Application complète, base temporaire, flotte vide."""
    monkeypatch.setenv("DISPATCH_DB_PATH", str(tmp_path / "api.db"))
    import app.services.storage as storage_module
    importlib.reload(storage_module)
    import app.services.comparaison as comparaison_module
    importlib.reload(comparaison_module)
    import app.api.routes_pilote as routes_module
    importlib.reload(routes_module)
    import app.main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient
    with TestClient(main_module.app) as c:
        c.post("/demo/reset")
        yield c


CORPS_COURSE = {
    "pickup_lat": 48.8566, "pickup_lon": 2.3522,
    "delivery_lat": 48.8864, "delivery_lon": 2.3432,
    "zone": "Paris", "volume_type": "Standard",
}


def _ajouter(client, code, vehicule="scoot_ville", lat=48.8566, lon=2.3522):
    return client.post("/coursiers", json={"code": code, "vehicle_type": vehicule, "lat": lat, "lon": lon})


class TestEndpointsPilote:

    def test_page_pilote_servie(self, client) -> None:
        r = client.get("/pilote")
        assert r.status_code == 200
        assert "Mode pilote" in r.text

    def test_comparaison_complete(self, client) -> None:
        _ajouter(client, "KEN")
        _ajouter(client, "MEH", lat=48.8864, lon=2.3432)

        r = client.post("/pilote/comparaison", json={**CORPS_COURSE, "choix_manuel": "MEH"})
        assert r.status_code == 201
        data = r.json()
        assert data["choix_app"] == "KEN"
        assert data["choix_manuel"] == "MEH"
        assert data["accord"] is False
        assert data["statistiques"]["total"] == 1
        assert len(data["classement"]) == 2

    def test_comparaison_attribue_au_choix_manuel(self, client) -> None:
        _ajouter(client, "KEN")
        _ajouter(client, "MEH", lat=48.8864, lon=2.3432)
        client.post("/pilote/comparaison", json={**CORPS_COURSE, "choix_manuel": "MEH"})

        flotte = {c["code"]: c for c in client.get("/coursiers").json()}
        assert flotte["MEH"]["order_count"] == 1
        assert flotte["KEN"]["order_count"] == 0

    def test_choix_manuel_inconnu_rejete(self, client) -> None:
        _ajouter(client, "KEN")
        r = client.post("/pilote/comparaison", json={**CORPS_COURSE, "choix_manuel": "ZZZ"})
        assert r.status_code == 404

    def test_simulation_ne_journalise_pas(self, client) -> None:
        _ajouter(client, "KEN")
        r = client.post("/pilote/simulation", json={**CORPS_COURSE, "choix_manuel": "KEN"})
        assert r.status_code == 200
        assert client.get("/pilote/journal").json()["statistiques"]["total"] == 0
        assert client.get("/coursiers").json()[0]["order_count"] == 0

    def test_journal_et_suppression(self, client) -> None:
        _ajouter(client, "KEN")
        entry_id = client.post("/pilote/comparaison", json={**CORPS_COURSE, "choix_manuel": "KEN"}).json()["journal_id"]

        journal = client.get("/pilote/journal").json()
        assert len(journal["entrees"]) == 1
        assert journal["statistiques"]["taux_accord"] == 100.0

        assert client.delete(f"/pilote/journal/{entry_id}").status_code == 200
        assert client.get("/pilote/journal").json()["statistiques"]["total"] == 0

    def test_export_csv(self, client) -> None:
        _ajouter(client, "KEN")
        client.post("/pilote/comparaison", json={**CORPS_COURSE, "choix_manuel": "KEN"})
        r = client.get("/pilote/journal/export.csv")
        assert r.status_code == 200
        assert "choix_manuel" in r.text and "KEN" in r.text

    def test_declarer_puis_cloturer_une_course(self, client) -> None:
        _ajouter(client, "KEN")
        r = client.post("/coursiers/KEN/courses", json={**CORPS_COURSE, "id": "EN-COURS-1"})
        assert r.status_code == 201
        assert r.json()["current_load"] == 1

        r = client.delete("/coursiers/KEN/courses/EN-COURS-1")
        assert r.status_code == 200
        assert r.json()["current_load"] == 0

    def test_cloture_d_une_course_absente(self, client) -> None:
        _ajouter(client, "KEN")
        assert client.delete("/coursiers/KEN/courses/INCONNUE").status_code == 404

    def test_course_declaree_pese_sur_le_classement(self, client) -> None:
        """Une charge déclarée doit se voir dans le score : c'est tout l'intérêt de la saisir."""
        _ajouter(client, "KEN")
        _ajouter(client, "MEH")     # même position, donc départage par la charge
        client.post("/coursiers/KEN/courses", json={**CORPS_COURSE, "id": "EN-COURS-1"})

        data = client.post("/pilote/simulation", json=CORPS_COURSE).json()
        assert data["choix_app"] == "MEH"    # KEN est chargé, MEH ne l'est pas
