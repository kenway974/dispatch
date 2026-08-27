"""
Situations réelles racontées par le terrain, transformées en règles vérifiables.

Chaque classe est UNE situation vécue, décrite dans les mots de celui qui la
vit. Le test échoue tant que le moteur ne décide pas comme lui. Une fois passé,
la règle est acquise pour toujours : aucun réglage futur ne pourra la casser en
silence.

C'est ce fichier qui fait foi sur le comportement attendu — pas un schéma.
"""

import pytest
from datetime import datetime, timedelta

from app.models.coursier import AssignedOrder, Coursier, GpsPosition
from app.models.enums import VehicleType, VolumeType, Zone
from app.models.order import Coordinates, Order
from app.services.dispatch import score_coursier
from app.services.fleet import FleetManager


# ── Paris, points de repère ───────────────────────────────────────────────────
SEIZIEME    = GpsPosition(lat=48.8629, lon=2.2875)   # Trocadéro
QUATRIEME   = GpsPosition(lat=48.8560, lon=2.3554)   # Marais
DIXIEME     = GpsPosition(lat=48.8760, lon=2.3590)   # Gare de l'Est
VINGTIEME   = GpsPosition(lat=48.8656, lon=2.3958)   # Père-Lachaise
MADELEINE   = GpsPosition(lat=48.8700, lon=2.3250)
NEUVIEME    = GpsPosition(lat=48.8709, lon=2.3317)   # Opéra
BOULOGNE    = GpsPosition(lat=48.8365, lon=2.2400)
HUITIEME    = GpsPosition(lat=48.8720, lon=2.3010)   # Champs-Élysées
TREIZIEME   = GpsPosition(lat=48.8312, lon=2.3555)
CINQUIEME   = GpsPosition(lat=48.8448, lon=2.3470)
SAINT_GERMAIN = GpsPosition(lat=48.8540, lon=2.3330)


def course_portee(
    ref: str,
    ramassage: GpsPosition,
    livraison: GpsPosition,
    ramassage_effectue: bool = False,
) -> AssignedOrder:
    return AssignedOrder(
        order_id=ref,
        pickup_lat=ramassage.lat, pickup_lon=ramassage.lon,
        delivery_lat=livraison.lat, delivery_lon=livraison.lon,
        volume_type=VolumeType.STANDARD,
        ramassage_effectue=ramassage_effectue,
    )


def commande(
    ref: str,
    ramassage: GpsPosition,
    livraison: GpsPosition,
    delai: int | None = None,
    minutes_ecoulees: float = 0,
    zone: Zone = Zone.PARIS,
) -> Order:
    return Order(
        id=ref,
        pickup=Coordinates(lat=ramassage.lat, lon=ramassage.lon),
        delivery=Coordinates(lat=livraison.lat, lon=livraison.lon),
        zone=zone, volume_type=VolumeType.STANDARD,
        deadline_minutes=delai,
        created_at=datetime.now() - timedelta(minutes=minutes_ecoulees),
    )


class TestRogerEtKarim:
    """
    Roger vient de ramasser dans le 16e, il livre dans le 4e — pas pressé.
    Il doit aussi ramasser dans le 10e pour livrer dans le 20e — celle-là est
    urgente. Il est en route, du côté de la Madeleine.

    Karim a ramassé quatre courses dans le 8e : trois pour le 13e, une pour le
    5e. Rien d'urgent, c'est « dans la journée ». Il est entre Concorde et
    Saint-Germain, il descend vers le sud.

    Une course urgente tombe : ramassage dans le 9e, livraison à Boulogne.

    Roger est juste à côté du ramassage. Karim est plus loin et devrait
    remonter. **C'est quand même Karim qui doit l'avoir** : Roger porte déjà
    une urgence, Karim ne porte que du souple. Ce n'est pas la distance qui
    autorise le détour, c'est ce qu'on a sur soi.
    """

    def _flotte(self) -> tuple[FleetManager, Coursier, Coursier]:
        fleet = FleetManager()

        roger = Coursier(
            code="ROG", vehicle_type=VehicleType.SCOOT_BANLIEUE_PROCHE, position=MADELEINE,
            assigned_orders=[
                # Déjà ramassée dans le 16e : il ne lui reste que la livraison
                course_portee("ROG-16-4", SEIZIEME, QUATRIEME, ramassage_effectue=True),
                # Pas encore ramassée, et celle-là presse
                course_portee("ROG-10-20", DIXIEME, VINGTIEME),
            ],
        )
        karim = Coursier(
            code="KAR", vehicle_type=VehicleType.SCOOT_BANLIEUE_PROCHE, position=SAINT_GERMAIN,
            assigned_orders=[
                course_portee(f"KAR-13-{i}", HUITIEME, TREIZIEME, ramassage_effectue=True)
                for i in range(3)
            ] + [course_portee("KAR-5", HUITIEME, CINQUIEME, ramassage_effectue=True)],
        )
        fleet.add_coursier(roger)
        fleet.add_coursier(karim)

        # Ce que chacun porte, vu du registre des commandes
        fleet.add_order(commande("ROG-16-4", SEIZIEME, QUATRIEME))                    # sans délai
        fleet.add_order(commande("ROG-10-20", DIXIEME, VINGTIEME, delai=45))          # urgente
        for i in range(3):
            fleet.add_order(commande(f"KAR-13-{i}", HUITIEME, TREIZIEME, delai=None))
        fleet.add_order(commande("KAR-5", HUITIEME, CINQUIEME, delai=None))
        return fleet, roger, karim

    def test_la_course_urgente_va_a_celui_qui_ne_porte_rien_de_presse(self) -> None:
        fleet, roger, karim = self._flotte()
        urgente = commande("NEUF-BOULOGNE", NEUVIEME, BOULOGNE, delai=45)

        ecart = score_coursier(roger, urgente, fleet) - score_coursier(karim, urgente, fleet)
        assert ecart > 5, (
            "Karim doit gagner largement : Roger porte déjà une urgence. "
            f"Écart obtenu : {ecart:.1f} km"
        )

    def test_c_est_bien_l_urgence_portee_qui_fait_la_difference(self) -> None:
        """
        Le même Roger, la même course, la même géographie — on ne change que le
        délai de ce qu'il transporte. Son score doit s'effondrer.
        """
        fleet, roger, _ = self._flotte()
        urgente = commande("NEUF-BOULOGNE", NEUVIEME, BOULOGNE, delai=45)

        avec_urgence_a_bord = score_coursier(roger, urgente, fleet)
        fleet.get_order("ROG-10-20").deadline_minutes = None      # sa course n'est plus pressée
        sans_urgence_a_bord = score_coursier(roger, urgente, fleet)

        assert sans_urgence_a_bord < avec_urgence_a_bord - 5

    def test_karim_perd_son_avantage_s_il_recoit_une_urgence(self) -> None:
        """
        Symétrique : dès que Karim porte lui aussi une urgence, il cesse d'être
        le candidat évident. Sa remise de coursier libre disparaît.
        """
        fleet, _, karim = self._flotte()
        urgente = commande("NEUF-BOULOGNE", NEUVIEME, BOULOGNE, delai=45)

        libre = score_coursier(karim, urgente, fleet)
        fleet.get_order("KAR-5").deadline_minutes = 20            # le voilà pressé
        occupe = score_coursier(karim, urgente, fleet)

        assert occupe > libre + 5

    def test_une_course_souple_n_est_pas_bloquee_par_une_urgence_a_bord(self) -> None:
        """
        Empiler du souple sur une urgence ne pose pas de problème en soi : la
        pénalité de cumul ne s'applique qu'entre deux urgences.
        """
        from app.services.dispatch import score_detail
        fleet, roger, _ = self._flotte()

        souple = score_detail(roger, commande("N", NEUVIEME, BOULOGNE, delai=None), fleet)
        pressee = score_detail(roger, commande("N", NEUVIEME, BOULOGNE, delai=45), fleet)

        assert souple.penalite_cumul_urgences == 0
        assert pressee.penalite_cumul_urgences > 0

    def test_roger_est_bien_le_plus_proche_du_ramassage(self) -> None:
        """Preuve que Karim ne gagne pas par accident de géographie."""
        from app.services.geo import haversine
        assert haversine(MADELEINE, NEUVIEME) < haversine(SAINT_GERMAIN, NEUVIEME)


class TestRamassageDejaFait:
    """
    Roger a déjà ramassé sa course du 16e. Son itinéraire ne doit plus repasser
    par le 16e — seule la livraison dans le 4e reste à faire.

    Sans ça, le moteur le croit obligé de retraverser Paris d'ouest en est pour
    un colis qu'il a déjà dans la sacoche, et tous ses détours sont faux.
    """

    def _roger(self, ramassage_effectue: bool) -> Coursier:
        return Coursier(
            code="ROG", vehicle_type=VehicleType.SCOOT_BANLIEUE_PROCHE, position=MADELEINE,
            assigned_orders=[course_portee("A", SEIZIEME, QUATRIEME, ramassage_effectue)],
        )

    def test_le_point_de_ramassage_disparait_de_l_itineraire(self) -> None:
        from app.services.dispatch import arrets_en_cours
        arrets = arrets_en_cours(self._roger(ramassage_effectue=True))
        assert len(arrets) == 1
        assert arrets[0].est_livraison is True

    def test_les_deux_points_restent_si_le_colis_n_est_pas_pris(self) -> None:
        from app.services.dispatch import arrets_en_cours
        assert len(arrets_en_cours(self._roger(ramassage_effectue=False))) == 2

    def test_la_tournee_restante_raccourcit(self) -> None:
        """
        Ne plus devoir retourner dans le 16e raccourcit ce qu'il lui reste à faire.
        C'est la conséquence directe, et la seule qui soit vraie dans tous les cas.
        """
        from app.services.dispatch import arrets_en_cours
        from app.services.geo import ordonner_tournee

        _, restant_avec = ordonner_tournee(MADELEINE, arrets_en_cours(self._roger(False)))
        _, restant_sans = ordonner_tournee(MADELEINE, arrets_en_cours(self._roger(True)))
        assert restant_sans < restant_avec

    def test_le_drapeau_change_bien_la_note(self) -> None:
        """
        Le score bouge — dans un sens qui dépend de la géographie, pas d'une règle.

        Ici il MONTE : tant que Roger devait remonter dans le 16e, une course
        vers Boulogne se fondait dans ce trajet vers l'ouest. Son colis récupéré,
        il repart vers l'est et Boulogne redevient un vrai crochet.
        """
        nouvelle = commande("N", NEUVIEME, BOULOGNE)
        assert score_coursier(self._roger(True), nouvelle) != score_coursier(self._roger(False), nouvelle)


# ═══════════════════════════════════════════════════════════════════════════
# Situation 2 — la pause, et le relais au bureau
# ═══════════════════════════════════════════════════════════════════════════

BUREAU        = GpsPosition(lat=48.8838, lon=2.3243)   # 24 rue des Dames, 17e
DIX_SEPTIEME  = GpsPosition(lat=48.8877, lon=2.3170)   # Batignolles
DEUXIEME      = GpsPosition(lat=48.8679, lon=2.3410)   # Bourse


class TestPauseImminente:
    """
    Kenny n'a pas encore mangé. Il devait prendre une course dans le 17e pour la
    livrer dans le 2e. Un collègue est en pause depuis trente minutes — il va
    bientôt reprendre.

    Ce que fait Kenny : il ramasse la course dans le 17e, la laisse au bureau
    (17e également), et part en pause. Le collègue, en rentrant, ira livrer
    dans le 2e.

    Deux choses en découlent, et le moteur ne connaît ni l'une ni l'autre :
    l'heure à laquelle un coursier s'arrête, et la possibilité de couper une
    course en deux.
    """

    def _duo(self, minutes_avant_pause: float):
        """Kenny au bureau, à quelques minutes de sa pause. Le collègue en rentre."""
        maintenant = datetime.now()
        fleet = FleetManager()

        kenny = Coursier(
            code="KEN", vehicle_type=VehicleType.SCOOT_BANLIEUE_PROCHE, position=BUREAU,
            debut_pause=maintenant + timedelta(minutes=minutes_avant_pause),
        )
        collegue = Coursier(
            code="COL", vehicle_type=VehicleType.SCOOT_BANLIEUE_PROCHE, position=BUREAU,
        )
        fleet.add_coursier(kenny)
        fleet.add_coursier(collegue)
        return fleet, kenny, collegue

    def test_celui_qui_part_en_pause_ne_prend_pas_ce_qu_il_ne_finira_pas(self) -> None:
        """
        La course 17e → 2e demande une bonne quinzaine de minutes. Kenny part
        manger dans cinq. Elle doit aller au collègue, à position identique.
        """
        fleet, kenny, collegue = self._duo(minutes_avant_pause=5)
        course = commande("C", DIX_SEPTIEME, DEUXIEME)

        assert score_coursier(collegue, course, fleet) < score_coursier(kenny, course, fleet)

    def test_avec_du_temps_devant_lui_kenny_la_prend_comme_avant(self) -> None:
        """Même situation, mais sa pause est dans deux heures : plus rien ne le retient."""
        fleet, kenny, collegue = self._duo(minutes_avant_pause=120)
        course = commande("C", DIX_SEPTIEME, DEUXIEME)

        ecart = abs(score_coursier(kenny, course, fleet) - score_coursier(collegue, course, fleet))
        assert ecart < 0.5, "à position et charge égales, rien ne doit les départager"

    def test_le_debordement_est_proportionnel(self) -> None:
        """Plus la pause est proche, plus la course lui coûte cher."""
        scores = []
        for minutes in (60, 20, 5):
            fleet, kenny, _ = self._duo(minutes_avant_pause=minutes)
            scores.append(score_coursier(kenny, commande("C", DIX_SEPTIEME, DEUXIEME), fleet))
        assert scores[0] <= scores[1] <= scores[2]

    def test_un_coursier_sans_horaire_declare_nest_jamais_penalise(self) -> None:
        """Tant que le dispatcheur n'a pas saisi les horaires, la règle dort."""
        from app.services.dispatch import score_detail
        fleet = FleetManager()
        libre = Coursier(code="LIB", vehicle_type=VehicleType.SCOOT_BANLIEUE_PROCHE, position=BUREAU)
        fleet.add_coursier(libre)
        detail = score_detail(libre, commande("C", DIX_SEPTIEME, DEUXIEME), fleet)
        assert detail.penalite_debordement == 0.0


class TestRelaisAuBureau:
    """
    La deuxième moitié de la situation : Kenny fait le ramassage, le collègue
    fait la livraison. Une seule course, deux coursiers.

    Le moteur en est incapable — il attribue une course entière ou rien. Ce test
    documente la limite au lieu de la laisser dans un coin de ma tête.
    """

    @pytest.mark.xfail(reason="Le moteur ne sait pas couper une course entre deux coursiers", strict=True)
    def test_le_ramassage_et_la_livraison_peuvent_aller_a_deux_coursiers(self) -> None:
        from app.services.comparaison import classer_coursiers   # noqa: F401
        raise AssertionError(
            "Il faudrait pouvoir attribuer le ramassage à KEN et la livraison à COL, "
            "avec le bureau comme point de passage."
        )
