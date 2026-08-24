"""
Cohérence d'une course avec la tournée en cours — les cas décrits par le terrain.

Le moteur ne note pas la distance jusqu'au ramassage, mais le **détour marginal** :
ce que la course rallonge à la tournée déjà engagée. C'est la question que le
coursier se pose réellement.

Chaque test reprend une situation vécue chez Lungta, avec ses vraies adresses.
Ils valent autant comme documentation des règles métier que comme filet : un
réglage du scoring qui casserait l'un d'eux casserait le jugement du dispatcheur.
"""

import pytest

from app.models.coursier import AssignedOrder, Coursier, GpsPosition
from app.models.enums import VehicleType, VolumeType, Zone
from app.models.order import Coordinates, Order
from app.services.dispatch import score_coursier
from app.services.geo import cout_insertion, detour_marginal


# ── Points de repère parisiens ────────────────────────────────────────────────
NATION      = GpsPosition(lat=48.8483, lon=2.3958)
CONCORDE    = GpsPosition(lat=48.8656, lon=2.3212)
REPUBLIQUE  = GpsPosition(lat=48.8675, lon=2.3636)
STRASBOURG  = GpsPosition(lat=48.8694, lon=2.3543)   # Strasbourg-Saint-Denis
NEUILLY     = GpsPosition(lat=48.8846, lon=2.2697)   # Neuilly-sur-Seine, ouest
VILLEJUIF   = GpsPosition(lat=48.7933, lon=2.3636)   # 94, plein sud
VOSGES      = GpsPosition(lat=48.8556, lon=2.3655)   # place des Vosges, 4e
BERTHIER    = GpsPosition(lat=48.8877, lon=2.3010)   # bd Berthier, 17e
TERNES      = GpsPosition(lat=48.8797, lon=2.2856)   # porte des Ternes, 17e
BUREAU      = GpsPosition(lat=48.8838, lon=2.3243)   # 24 rue des Dames, 17e
DOUZIEME    = GpsPosition(lat=48.8409, lon=2.3876)
BASTILLE    = GpsPosition(lat=48.8533, lon=2.3692)
DIX_HUITIEME= GpsPosition(lat=48.8890, lon=2.3450)
BOETIE      = GpsPosition(lat=48.8712, lon=2.3092)   # 106 rue de la Boétie, 8e


def coursier(code: str, position: GpsPosition, tournee: list[tuple[GpsPosition, GpsPosition]] | None = None) -> Coursier:
    """Coursier avec sa tournée en cours, décrite comme une suite (ramassage, livraison)."""
    return Coursier(
        code=code,
        vehicle_type=VehicleType.SCOOT_BANLIEUE_PROCHE,
        position=position,
        assigned_orders=[
            AssignedOrder(
                order_id=f"{code}-{i}",
                pickup_lat=p.lat, pickup_lon=p.lon,
                delivery_lat=d.lat, delivery_lon=d.lon,
                volume_type=VolumeType.STANDARD,
            )
            for i, (p, d) in enumerate(tournee or [])
        ],
    )


def course(ramassage: GpsPosition, livraison: GpsPosition, zone: Zone = Zone.PARIS) -> Order:
    return Order(
        id="NOUVELLE",
        pickup=Coordinates(lat=ramassage.lat, lon=ramassage.lon),
        delivery=Coordinates(lat=livraison.lat, lon=livraison.lon),
        zone=zone,
        volume_type=VolumeType.STANDARD,
    )


class TestDirectionDuTrajet:
    """
    « Je ramasse à Nation, je livre à Concorde. Je suis à République. Une course
    tombe à Strasbourg-Saint-Denis : si elle va à Neuilly je peux la prendre,
    c'est cohérent. Si elle redescend à Villejuif, c'est un détour de malade. »

    Même ramassage dans les deux cas — seule la destination change. Une notation
    fondée sur la distance au ramassage les jugerait identiques.
    """

    def test_livraison_dans_le_sens_de_la_marche_est_preferee(self) -> None:
        vers_neuilly   = detour_marginal(REPUBLIQUE, [CONCORDE], STRASBOURG, NEUILLY)
        vers_villejuif = detour_marginal(REPUBLIQUE, [CONCORDE], STRASBOURG, VILLEJUIF)
        assert vers_neuilly < vers_villejuif

    def test_une_course_dans_le_sens_de_la_marche_peut_raccourcir_la_tournee(self) -> None:
        """Neuilly prolonge la trajectoire ouest : le détour est négatif."""
        assert detour_marginal(REPUBLIQUE, [CONCORDE], STRASBOURG, NEUILLY) < 0

    def test_le_ramassage_seul_ne_departagerait_pas(self) -> None:
        """
        Preuve que le critère « distance au ramassage » est insuffisant : il est
        rigoureusement identique dans les deux situations.
        """
        from app.services.geo import haversine
        assert haversine(REPUBLIQUE, STRASBOURG) == pytest.approx(
            haversine(REPUBLIQUE, STRASBOURG)
        )
        # ... et pourtant les deux courses ne se valent pas
        assert detour_marginal(REPUBLIQUE, [CONCORDE], STRASBOURG, NEUILLY) != pytest.approx(
            detour_marginal(REPUBLIQUE, [CONCORDE], STRASBOURG, VILLEJUIF)
        )


class TestPassageAuBureau:
    """
    « Une course à prendre au bureau, rue des Dames, pour le 12e. Le coursier qui
    a déjà Berthier et la porte des Ternes dans le 17e ne va pas faire l'aller-
    retour : il passe au bureau au passage. »
    """

    def test_le_coursier_qui_traverse_le_secteur_est_choisi(self) -> None:
        vers_le_17e = coursier("AAA", VOSGES, [(VOSGES, BERTHIER), (BERTHIER, TERNES)])
        vers_l_est  = coursier("BBB", NATION, [(NATION, BASTILLE)])
        nouvelle    = course(BUREAU, DOUZIEME)

        assert score_coursier(vers_le_17e, nouvelle) < score_coursier(vers_l_est, nouvelle)


class TestRegroupementParDestination:
    """
    « J'avais des courses pour le 106 rue de la Boétie. On m'a dit de passer au
    bureau en prendre d'autres qui allaient à la même adresse. »

    Plusieurs courses vers la même destination doivent voyager ensemble : celui
    qui y va déjà ne paie presque rien pour une de plus.
    """

    def test_celui_qui_va_deja_a_l_adresse_est_choisi(self) -> None:
        deja_boetie = coursier("KEN", DIX_HUITIEME, [(BUREAU, BOETIE)])
        ailleurs    = coursier("AUT", NATION, [(NATION, BASTILLE)])
        nouvelle    = course(BUREAU, BOETIE)

        assert score_coursier(deja_boetie, nouvelle) < score_coursier(ailleurs, nouvelle)

    def test_une_course_identique_ne_coute_presque_rien(self) -> None:
        """
        Même ramassage, même livraison : la tournée ne s'allonge pas d'un mètre.

        C'est le coût d'insertion BRUT qui vaut zéro. Le détour marginal, lui,
        vaut moins que zéro : on lui a retranché le trajet propre de la course,
        que le coursier n'a pas à refaire puisqu'il le fait déjà.
        """
        assert cout_insertion(BUREAU, [BUREAU, BOETIE], BUREAU, BOETIE) == pytest.approx(0.0, abs=0.01)
        assert detour_marginal(BUREAU, [BUREAU, BOETIE], BUREAU, BOETIE) < 0

    def test_le_regroupement_l_emporte_sur_la_penalite_de_charge(self) -> None:
        """
        Un coursier chargé qui fait déjà le trajet bat un coursier vide qui ne le
        fait pas. C'est voulu : trois pochettes pour la même adresse dans la même
        sacoche valent mieux que trois coursiers qui s'y rendent séparément.
        """
        charge_mais_sur_place = coursier("KEN", BUREAU, [(BUREAU, BOETIE)] * 3)
        vide_mais_loin        = coursier("AUT", NATION)
        nouvelle              = course(BUREAU, BOETIE)

        assert score_coursier(charge_mais_sur_place, nouvelle) < score_coursier(vide_mais_loin, nouvelle)


class TestCoursierAuRepos:
    """Sans tournée en cours, le détour se réduit à la distance jusqu'au ramassage."""

    def test_detour_egale_distance_au_ramassage(self) -> None:
        from app.services.geo import haversine
        assert detour_marginal(NATION, [], BUREAU, DOUZIEME) == pytest.approx(
            haversine(NATION, BUREAU), rel=1e-6
        )

    def test_le_plus_proche_gagne_entre_deux_coursiers_au_repos(self) -> None:
        proche = coursier("PRO", BUREAU)
        loin   = coursier("LOI", NATION)
        nouvelle = course(BUREAU, DOUZIEME)
        assert score_coursier(proche, nouvelle) < score_coursier(loin, nouvelle)
