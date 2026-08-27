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
from app.services.geo import Arret, cout_insertion, detour_marginal


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
        vehicle_type=VehicleType.SCOOT_50,
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


def arrets(*courses: tuple[GpsPosition, GpsPosition]) -> list[Arret]:
    """Traduit une suite (ramassage, livraison) en arrêts, sans en figer l'ordre."""
    resultat: list[Arret] = []
    for i, (ramassage, livraison) in enumerate(courses):
        resultat.append(Arret(ramassage, f"C{i}", est_livraison=False))
        resultat.append(Arret(livraison, f"C{i}", est_livraison=True))
    return resultat


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
        vers_neuilly   = detour_marginal(REPUBLIQUE, [Arret(CONCORDE, 'EN_COURS', est_livraison=True)], STRASBOURG, NEUILLY)
        vers_villejuif = detour_marginal(REPUBLIQUE, [Arret(CONCORDE, 'EN_COURS', est_livraison=True)], STRASBOURG, VILLEJUIF)
        assert vers_neuilly < vers_villejuif

    def test_une_course_dans_le_sens_de_la_marche_est_largement_absorbee(self) -> None:
        """
        Neuilly prolonge la trajectoire ouest déjà engagée : l'essentiel du trajet
        se fond dans la tournée. Le détour doit rester une petite fraction de ce
        que la course coûterait à un coursier qui partirait exprès.
        """
        from app.services.geo import haversine
        tournee = [Arret(CONCORDE, "EN_COURS", est_livraison=True)]
        detour  = detour_marginal(REPUBLIQUE, tournee, STRASBOURG, NEUILLY)
        trajet  = haversine(STRASBOURG, NEUILLY)
        assert detour < trajet / 2

    def test_une_course_a_contresens_nest_pas_absorbee(self) -> None:
        """Villejuif repart plein sud : rien ne se fond, le détour explose."""
        from app.services.geo import haversine
        tournee = [Arret(CONCORDE, "EN_COURS", est_livraison=True)]
        detour  = detour_marginal(REPUBLIQUE, tournee, STRASBOURG, VILLEJUIF)
        assert detour > haversine(STRASBOURG, NEUILLY) / 2

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
        assert detour_marginal(REPUBLIQUE, [Arret(CONCORDE, 'EN_COURS', est_livraison=True)], STRASBOURG, NEUILLY) != pytest.approx(
            detour_marginal(REPUBLIQUE, [Arret(CONCORDE, 'EN_COURS', est_livraison=True)], STRASBOURG, VILLEJUIF)
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
        assert cout_insertion(BUREAU, arrets((BUREAU, BOETIE)), arrets((BUREAU, BOETIE))) == pytest.approx(0.0, abs=0.01)
        assert detour_marginal(BUREAU, arrets((BUREAU, BOETIE)), BUREAU, BOETIE) < 0

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


class TestProtectionDesUrgences:
    """
    Un coursier à qui il reste peu de temps pour honorer une livraison promise
    ne doit pas être chargé d'autre chose.

    Ce qui compte n'est pas « porte-t-il une urgence ? » mais « lui reste-t-il
    assez de temps ? ». Beaucoup de marge : il peut prendre un détour. Plus de
    marge : on le laisse tranquille. Et si la course est sur son axe, elle ne le
    retarde pas — il la prend au passage, quitte à confier la livraison à un
    autre.
    """

    def _coursier_sous_echeance(self, minutes_restantes: float, livraison: GpsPosition):
        from datetime import datetime, timedelta
        from app.services.fleet import FleetManager

        fleet = FleetManager()
        c = coursier("URG", REPUBLIQUE, [(REPUBLIQUE, livraison)])
        fleet.add_coursier(c)
        fleet.add_order(Order(
            id="URG-0",
            pickup=Coordinates(lat=REPUBLIQUE.lat, lon=REPUBLIQUE.lon),
            delivery=Coordinates(lat=livraison.lat, lon=livraison.lon),
            zone=Zone.PARIS,
            volume_type=VolumeType.STANDARD,
            deadline_minutes=90,
            created_at=datetime.now() - timedelta(minutes=90 - minutes_restantes),
        ))
        return fleet, c

    def test_peu_de_marge_une_course_detournante_est_penalisee(self) -> None:
        from app.services.dispatch import score_detail
        fleet, c = self._coursier_sous_echeance(30, NEUILLY)
        detail = score_detail(c, course(NATION, VILLEJUIF, zone=Zone.PETITE_COURONNE), fleet)
        assert detail.penalite_retard > 0
        assert detail.motif_retard and "URG-0" in detail.motif_retard

    def test_beaucoup_de_marge_aucune_penalite(self) -> None:
        """
        Trois heures devant lui : rien à protéger.

        Le seuil a monté depuis que le moteur compte le temps passé sur place et
        majore les trajets — une tournée dure plus longtemps qu'à vol d'oiseau.
        """
        from app.services.dispatch import score_detail
        fleet, c = self._coursier_sous_echeance(180, NEUILLY)
        detail = score_detail(c, course(NATION, VILLEJUIF, zone=Zone.PETITE_COURONNE), fleet)
        assert detail.penalite_retard == pytest.approx(0.0, abs=0.01)

    def test_seule_la_marge_restante_change_le_score(self) -> None:
        """Tout est identique par ailleurs : c'est bien le temps disponible qui décide."""
        from app.services.dispatch import score_detail
        detournante = course(NATION, VILLEJUIF, zone=Zone.PETITE_COURONNE)
        fleet_court, c_court = self._coursier_sous_echeance(30, NEUILLY)
        fleet_long,  c_long  = self._coursier_sous_echeance(180, NEUILLY)
        assert score_detail(c_court, detournante, fleet_court).total > \
               score_detail(c_long,  detournante, fleet_long).total

    def test_une_course_sur_son_axe_coute_bien_moins_qu_un_detour(self) -> None:
        """
        Marge tout aussi courte. Une course qui suit son axe le retarde à peine ;
        une course à contresens le fait exploser. C'est l'exception du terrain —
        on prend au passage, quitte à confier la livraison à un autre.
        """
        from app.services.dispatch import score_detail
        fleet, c = self._coursier_sous_echeance(30, NEUILLY)

        sur_l_axe    = score_detail(c, course(STRASBOURG, NEUILLY), fleet)
        a_contresens = score_detail(c, course(NATION, VILLEJUIF, zone=Zone.PETITE_COURONNE), fleet)

        assert sur_l_axe.penalite_retard < a_contresens.penalite_retard / 3

    def test_sans_echeance_aucune_protection_ne_sapplique(self) -> None:
        from app.services.dispatch import score_detail
        from app.services.fleet import FleetManager

        fleet = FleetManager()
        c = coursier("LIB", REPUBLIQUE, [(REPUBLIQUE, NEUILLY)])
        fleet.add_coursier(c)
        detail = score_detail(c, course(NATION, VILLEJUIF, zone=Zone.PETITE_COURONNE), fleet)
        assert detail.penalite_retard == 0.0
