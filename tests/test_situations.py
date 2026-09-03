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
            code="ROG", vehicle_type=VehicleType.SCOOT_50, position=MADELEINE,
            assigned_orders=[
                # Déjà ramassée dans le 16e : il ne lui reste que la livraison
                course_portee("ROG-16-4", SEIZIEME, QUATRIEME, ramassage_effectue=True),
                # Pas encore ramassée, et celle-là presse
                course_portee("ROG-10-20", DIXIEME, VINGTIEME),
            ],
        )
        karim = Coursier(
            code="KAR", vehicle_type=VehicleType.SCOOT_50, position=SAINT_GERMAIN,
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
            code="ROG", vehicle_type=VehicleType.SCOOT_50, position=MADELEINE,
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
            code="KEN", vehicle_type=VehicleType.SCOOT_50, position=BUREAU,
            debut_pause=maintenant + timedelta(minutes=minutes_avant_pause),
        )
        collegue = Coursier(
            code="COL", vehicle_type=VehicleType.SCOOT_50, position=BUREAU,
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
        libre = Coursier(code="LIB", vehicle_type=VehicleType.SCOOT_50, position=BUREAU)
        fleet.add_coursier(libre)
        detail = score_detail(libre, commande("C", DIX_SEPTIEME, DEUXIEME), fleet)
        assert detail.penalite_debordement == 0.0


class TestReattribution:
    """
    La seconde moitié de la situation, telle qu'elle se passe vraiment.

    Le dispatcheur a prévu le coup : « tu ramasses dans le 17e, tu viens prendre
    ta pause, tu donnes le colis à ton collègue qui ira dans le 2e ». Il reprend
    la course déjà attribuée à Kenny et la bascule sur le collègue.

    Ce n'est donc pas une course coupée en deux : c'est une course qui change de
    titulaire. Le colis passe de main à main sur place — les deux sont au même
    endroit — et rien n'attend nulle part.

    Le point de passation n'est pas le bureau : c'est là où les deux se croisent.
    Ici c'est le bureau parce que Kenny y prend sa pause, mais loin du 17e ce
    serait ailleurs.
    """

    def _situation(self, minutes_avant_pause: float = 5, distance_collegue_km: float = 0.0):
        """Kenny porte la course, sa pause approche. Le collègue est à côté."""
        maintenant = datetime.now()
        fleet = FleetManager()

        kenny = Coursier(
            code="KEN", vehicle_type=VehicleType.SCOOT_50, position=BUREAU,
            debut_pause=maintenant + timedelta(minutes=minutes_avant_pause),
            assigned_orders=[
                course_portee("C17-2", DIX_SEPTIEME, DEUXIEME, ramassage_effectue=True)
            ],
        )
        # ~0,009° de latitude ≈ 1 km
        collegue = Coursier(
            code="COL", vehicle_type=VehicleType.SCOOT_50,
            position=GpsPosition(lat=BUREAU.lat + distance_collegue_km * 0.009, lon=BUREAU.lon),
        )
        fleet.add_coursier(kenny)
        fleet.add_coursier(collegue)
        fleet.add_order(commande("C17-2", DIX_SEPTIEME, DEUXIEME))
        return fleet

    def test_le_moteur_propose_de_passer_la_course_au_collegue(self) -> None:
        from app.services.reattribution import proposer_echanges
        propositions = proposer_echanges(self._situation())

        assert len(propositions) == 1
        p = propositions[0]
        assert p.order_id == "C17-2"
        assert p.porteur == "KEN"
        assert p.repreneur == "COL"
        assert p.gain_km > 0

    def test_le_motif_dit_pourquoi(self) -> None:
        from app.services.reattribution import proposer_echanges
        assert "pause" in proposer_echanges(self._situation())[0].motif

    def test_rien_a_proposer_si_kenny_a_le_temps(self) -> None:
        """Sa pause est dans deux heures : il n'y a aucune raison de lui reprendre."""
        from app.services.reattribution import proposer_echanges
        assert proposer_echanges(self._situation(minutes_avant_pause=120)) == []

    def test_il_faut_pouvoir_se_passer_le_colis_de_la_main_a_la_main(self) -> None:
        """
        Un collègue à trois kilomètres ne sert à rien : le colis est dans la
        sacoche de Kenny, il faut qu'ils soient au même endroit.
        """
        from app.services.reattribution import proposer_echanges
        assert proposer_echanges(self._situation(distance_collegue_km=3)) == []

    def test_le_point_de_passation_est_la_ou_ils_se_croisent(self) -> None:
        """
        Pas le bureau par principe : la position commune des deux. Ici c'est le
        bureau parce que Kenny y prend sa pause, ailleurs ce serait ailleurs.
        """
        from app.services.geo import haversine
        from app.services.reattribution import proposer_echanges
        p = proposer_echanges(self._situation())[0]
        assert haversine(p.point_passation, BUREAU) < 0.1

    def test_un_gain_derisoire_ne_declenche_rien(self) -> None:
        """Se passer un colis coûte du temps à deux personnes : il faut que ça vaille le coup."""
        from app.services.reattribution import proposer_echanges
        assert proposer_echanges(self._situation(), gain_minimum_km=999) == []


# ═══════════════════════════════════════════════════════════════════════════
# Situation 3 — la crevaison
# ═══════════════════════════════════════════════════════════════════════════

CONCORDE_S3 = GpsPosition(lat=48.8656, lon=2.3212)


class TestCoursierImmobilise:
    """
    « J'ai crevé et j'ai quatre courses sur moi. J'appelle le dispatch pour
    prévenir, et ensuite on s'arrange avec un collègue qui est proche ou un
    collègue qui n'a pas beaucoup de courses. »

    Différence avec une passation ordinaire : le collègue vient à lui. La
    contrainte « à portée de bras » ne tient plus, et c'est tout le portefeuille
    qui part, pas une course.
    """

    def _flotte_avec_panne(self):
        fleet = FleetManager()
        panne = Coursier(
            code="PAN", vehicle_type=VehicleType.SCOOT_50, position=CONCORDE_S3,
            assigned_orders=[
                course_portee(f"P{i}", CONCORDE_S3, DEUXIEME, ramassage_effectue=True)
                for i in range(4)
            ],
        )
        proche = Coursier(code="PRO", vehicle_type=VehicleType.SCOOT_50,
                          position=GpsPosition(lat=48.8700, lon=2.3300))
        charge = Coursier(
            code="CHA", vehicle_type=VehicleType.SCOOT_50, position=CONCORDE_S3,
            assigned_orders=[
                course_portee(f"C{i}", CONCORDE_S3, DEUXIEME, ramassage_effectue=True)
                for i in range(4)
            ],
        )
        for c in (panne, proche, charge):
            fleet.add_coursier(c)
        for i in range(4):
            fleet.add_order(commande(f"P{i}", CONCORDE_S3, DEUXIEME))
            fleet.add_order(commande(f"C{i}", CONCORDE_S3, DEUXIEME))
        return fleet

    def test_tout_le_portefeuille_est_redistribue(self) -> None:
        from app.services.reattribution import delester_coursier
        transferts = delester_coursier(self._flotte_avec_panne(), "PAN")
        assert len(transferts) == 4

    def test_le_collegue_le_moins_charge_prend_le_gros(self) -> None:
        """
        « un collègue qui est proche ou un collègue qui n'a pas beaucoup de courses »

        Pas tout sur le même : la charge du repreneur monte au fur et à mesure,
        et à partir d'un certain point un autre devient meilleur. C'est voulu —
        déverser quatre courses sur un seul dos ne ferait que déplacer le problème.
        """
        from app.services.reattribution import delester_coursier
        transferts = delester_coursier(self._flotte_avec_panne(), "PAN")
        repris_par_le_libre = sum(1 for t in transferts if t.repreneur == "PRO")
        assert repris_par_le_libre > len(transferts) / 2

    def test_la_flotte_est_reellement_mise_a_jour(self) -> None:
        """Ce n'est pas une proposition : le coursier est en panne, il faut agir."""
        from app.services.reattribution import delester_coursier
        fleet = self._flotte_avec_panne()
        delester_coursier(fleet, "PAN")
        assert fleet.get_coursier("PAN").order_count == 0
        assert fleet.get_coursier("PRO").order_count + fleet.get_coursier("CHA").order_count == 8

    def test_le_coursier_en_panne_est_mis_hors_service(self) -> None:
        from app.services.reattribution import delester_coursier
        fleet = self._flotte_avec_panne()
        delester_coursier(fleet, "PAN")
        assert fleet.get_coursier("PAN").is_active is False

    def test_la_distance_ne_bloque_pas_le_delestage(self) -> None:
        """Le collègue se déplace : on n'exige pas qu'ils soient déjà côte à côte."""
        from app.services.reattribution import delester_coursier
        fleet = self._flotte_avec_panne()
        fleet.update_coursier_position("PRO", 48.9360, 2.3553)   # Saint-Denis, loin
        assert len(delester_coursier(fleet, "PAN")) == 4


class TestMemeClientQuiRappelle:
    """
    « Le client part de Concorde, je viens ramasser, je suis parti. Une deuxième
    course arrive. On privilégie le coursier qui a déjà pris la première, s'il
    n'est pas trop loin du point de ramassage et s'il n'y a pas de
    contre-indication. »
    """

    def _flotte(self, distance_parcourue_km: float = 0.5):
        fleet = FleetManager()
        # Il a ramassé à Concorde et vient de démarrer
        parti = Coursier(
            code="PAR", vehicle_type=VehicleType.SCOOT_50,
            position=GpsPosition(lat=CONCORDE_S3.lat + distance_parcourue_km * 0.009, lon=CONCORDE_S3.lon),
            assigned_orders=[course_portee("PREMIERE", CONCORDE_S3, DEUXIEME, ramassage_effectue=True)],
        )
        # Un collègue à la même distance du ramassage, mais qui n'y est jamais allé
        autre = Coursier(
            code="AUT", vehicle_type=VehicleType.SCOOT_50,
            position=GpsPosition(lat=CONCORDE_S3.lat - distance_parcourue_km * 0.009, lon=CONCORDE_S3.lon),
        )
        fleet.add_coursier(parti)
        fleet.add_coursier(autre)
        fleet.add_order(commande("PREMIERE", CONCORDE_S3, DEUXIEME))
        return fleet, parti, autre

    def test_celui_qui_vient_de_ramasser_la_reprend(self) -> None:
        fleet, parti, autre = self._flotte()
        seconde = commande("SECONDE", CONCORDE_S3, DEUXIEME)
        assert score_coursier(parti, seconde, fleet) < score_coursier(autre, seconde, fleet)

    def test_son_avantage_fond_a_mesure_qu_il_s_eloigne(self) -> None:
        """
        « s'il n'est pas trop loin du point de ramassage ».

        Le moteur mesure mieux que la distance : il regarde si le retour au
        ramassage reste cohérent avec la suite de sa tournée. L'avantage
        s'érode donc progressivement au lieu de disparaître à un seuil.
        """
        fleet_proche, proche, _ = self._flotte(distance_parcourue_km=0.5)
        fleet_loin, loin, _ = self._flotte(distance_parcourue_km=6)
        seconde = commande("SECONDE", CONCORDE_S3, DEUXIEME)

        assert score_coursier(proche, seconde, fleet_proche) < score_coursier(loin, seconde, fleet_loin)


# ═══════════════════════════════════════════════════════════════════════════
# Situation 4 — le moteur n'a pas toujours à trancher
# ═══════════════════════════════════════════════════════════════════════════

DEPOT = GpsPosition(lat=48.8838, lon=2.3243)      # 24 rue des Dames, 17e
CRETEIL = GpsPosition(lat=48.7773, lon=2.4555)


class TestFinDeServiceProche:
    """
    « Ça me ferait péter un câble qu'un robot me dise que non, en fait, tu n'as
    pas fini, tu continues de travailler. Si une course arrive et qu'on
    l'attribuerait à un coursier qui termine dans moins de trente minutes, on
    laisse le choix au dispatch, qui négociera avec le coursier. »

    Le moteur cesse donc de trancher seul dans ce cas. Il désigne toujours le
    meilleur candidat, mais il le marque : à valider avec l'intéressé.
    """

    def _coursier(self, minutes_avant_fin: float) -> Coursier:
        return Coursier(
            code="FIN", vehicle_type=VehicleType.SCOOT_50, position=DEUXIEME,
            fin_service=datetime.now() + timedelta(minutes=minutes_avant_fin),
        )

    def test_moins_de_trente_minutes_le_dispatcheur_tranche(self) -> None:
        from app.services.dispatch import score_detail
        detail = score_detail(self._coursier(20), commande("C", DIX_SEPTIEME, DEUXIEME))
        assert detail.validation_requise is True
        assert "termine" in detail.motif_validation

    def test_avec_du_temps_le_moteur_decide_seul(self) -> None:
        from app.services.dispatch import score_detail
        detail = score_detail(self._coursier(180), commande("C", DIX_SEPTIEME, DEUXIEME))
        assert detail.validation_requise is False

    def test_sans_horaire_declare_aucune_validation(self) -> None:
        from app.services.dispatch import score_detail
        libre = Coursier(code="LIB", vehicle_type=VehicleType.SCOOT_50, position=DEUXIEME)
        assert score_detail(libre, commande("C", DIX_SEPTIEME, DEUXIEME)).validation_requise is False


class TestTrajetRetour:
    """
    « Je finis à Créteil, je rentre au local. S'il y a une course qui tombe et
    que je peux la ramasser sur la route, je la ramasse. Il ne faut pas que ça
    perturbe mon trajet retour. Si ça ne perturbe pas, on peut l'attribuer ; si
    ça perturbe un peu, on laisse le choix au dispatch. »
    """

    def _rentrant(self) -> Coursier:
        """À Créteil, il termine dans une heure et rentre au dépôt du 17e."""
        return Coursier(
            code="RET", vehicle_type=VehicleType.SCOOT_125, position=CRETEIL,
            fin_service=datetime.now() + timedelta(minutes=60),
            retour_depot=DEPOT,
        )

    def test_une_course_sur_le_trajet_retour_est_moins_chere_qu_un_contresens(self) -> None:
        """
        Créteil → Nation → 17e, c'est sa route. Créteil → Marne-la-Vallée, c'est
        plein est, à l'opposé du dépôt.
        """
        from app.services.dispatch import score_detail
        rentrant = self._rentrant()
        sur_la_route = score_detail(rentrant, commande("C", NATION_S4, DIX_SEPTIEME))
        a_contresens = score_detail(rentrant, commande("C", CRETEIL, MARNE, zone=Zone.GRANDE_COURONNE))
        assert sur_la_route.total < a_contresens.total

    def test_une_course_sur_sa_route_ne_demande_aucune_validation(self) -> None:
        from app.services.dispatch import score_detail
        assert score_detail(self._rentrant(), commande("C", NATION_S4, DIX_SEPTIEME)).validation_requise is False

    def test_un_contresens_rend_la_main_au_dispatcheur(self) -> None:
        """« si ça perturbe un peu le trajet retour, on laisse le choix au dispatch »"""
        from app.services.dispatch import score_detail
        detail = score_detail(self._rentrant(), commande("C", CRETEIL, MARNE, zone=Zone.GRANDE_COURONNE))
        assert detail.validation_requise is True
        assert "trajet retour" in detail.motif_validation

    def test_le_motif_annonce_le_meme_detour_que_le_classement(self) -> None:
        """Le dispatcheur ne doit pas lire deux nombres différents pour la même chose."""
        from app.services.dispatch import score_detail
        detail = score_detail(self._rentrant(), commande("C", CRETEIL, MARNE, zone=Zone.GRANDE_COURONNE))
        assert f"{detail.detour_km:.1f}" in detail.motif_validation

    def test_sans_depot_declare_le_retour_nest_pas_compte(self) -> None:
        """Tant que le dispatcheur n'a rien saisi, on n'invente pas un trajet retour."""
        from app.services.dispatch import arrets_en_cours
        sans = Coursier(code="SAN", vehicle_type=VehicleType.SCOOT_125, position=CRETEIL)
        assert arrets_en_cours(sans) == []


NATION_S4 = GpsPosition(lat=48.8483, lon=2.3958)
MARNE = GpsPosition(lat=48.8420, lon=2.7870)      # plein est, à l'opposé du dépôt
VILLEJUIF_S4 = GpsPosition(lat=48.7933, lon=2.3636)


class TestDisponibiliteClient:
    """
    « Il faut ajouter la notion de disponibilité chez les clients : les horaires
    dans lesquels on peut les livrer, et ne pas les livrer. »

    Une entreprise qui ferme à midi ne se livre pas à 14h, quel que soit le
    coursier. C'est un filtre, pas une pénalité.
    """

    def _course_avec_fenetre(self, ouverture: str, fermeture: str) -> Order:
        c = commande("C", DIX_SEPTIEME, DEUXIEME)
        c.livraison_ouverture = ouverture
        c.livraison_fermeture = fermeture
        return c

    def test_hors_plage_le_coursier_est_ecarte(self) -> None:
        from app.services.dispatch import motif_inegibilite
        c = Coursier(code="KEN", vehicle_type=VehicleType.SCOOT_50, position=DIX_SEPTIEME)
        # Une fenêtre déjà refermée : personne ne peut plus livrer
        motif = motif_inegibilite(c, self._course_avec_fenetre("00:00", "00:01"))
        assert motif is not None and "ferm" in motif.lower()

    def test_dans_la_plage_tout_va_bien(self) -> None:
        from app.services.dispatch import motif_inegibilite
        c = Coursier(code="KEN", vehicle_type=VehicleType.SCOOT_50, position=DIX_SEPTIEME)
        assert motif_inegibilite(c, self._course_avec_fenetre("00:00", "23:59")) is None

    def test_sans_plage_declaree_aucune_contrainte(self) -> None:
        from app.services.dispatch import motif_inegibilite
        c = Coursier(code="KEN", vehicle_type=VehicleType.SCOOT_50, position=DIX_SEPTIEME)
        assert motif_inegibilite(c, commande("C", DIX_SEPTIEME, DEUXIEME)) is None


class TestAttenteSurPlace:
    """
    « Si on attend trente minutes sur place, ça reste toujours une course, mais
    on facture l'attente. »

    Une seule course, donc. Mais ces trente minutes existent dans la journée du
    coursier et doivent peser sur ce qu'on lui donne ensuite.
    """

    def test_l_attente_declaree_alourdit_la_tournee(self) -> None:
        from app.services.dispatch import score_detail
        base = Coursier(code="ATT", vehicle_type=VehicleType.SCOOT_50, position=DIX_SEPTIEME,
                        fin_service=datetime.now() + timedelta(minutes=45))
        sans = commande("C", DIX_SEPTIEME, DEUXIEME)
        avec = commande("C", DIX_SEPTIEME, DEUXIEME)
        avec.minutes_attente = 30

        assert score_detail(base, avec).total > score_detail(base, sans).total


class TestLabelTournee:
    """
    « On doit pouvoir avoir un label tournée, une propriété qui indique qu'une
    course fait partie d'une tournée ou si c'est une course à course. »

    Et le coursier en tournée reste dispatchable tant qu'il n'est pas surchargé :
    celui qui a neuf restaurants du 9e est indisponible, celui qui a trois
    livraisons dans le 13e peut encore prendre une course qui va dans le 13e.
    """

    def test_une_course_porte_son_label(self) -> None:
        c = commande("C", DIX_SEPTIEME, DEUXIEME)
        c.tournee = "compta-lundi-8e"
        assert c.tournee == "compta-lundi-8e"

    def test_par_defaut_une_course_est_a_la_course(self) -> None:
        assert commande("C", DIX_SEPTIEME, DEUXIEME).tournee is None

    def test_un_coursier_en_tournee_legere_reste_dispatchable(self) -> None:
        """Trois livraisons dans le 13e : il peut encore prendre une course qui y va."""
        from app.services.dispatch import motif_inegibilite
        en_tournee = Coursier(
            code="TRN", vehicle_type=VehicleType.SCOOT_50, position=DIX_SEPTIEME,
            assigned_orders=[course_portee(f"T{i}", DIX_SEPTIEME, DEUXIEME, True) for i in range(3)],
        )
        assert motif_inegibilite(en_tournee, commande("C", DEUXIEME, DEUXIEME)) is None

    def test_un_coursier_en_tournee_chargee_ne_l_est_plus(self) -> None:
        """Neuf restaurants du 9e : sa capacité le sort d'elle-même du classement."""
        from app.services.dispatch import motif_inegibilite
        charge = Coursier(
            code="TRN", vehicle_type=VehicleType.SCOOT_50, position=DIX_SEPTIEME,
            assigned_orders=[course_portee(f"T{i}", DIX_SEPTIEME, DEUXIEME, True) for i in range(5)],
        )
        assert motif_inegibilite(charge, commande("C", DEUXIEME, DEUXIEME)) is not None


class TestTourneeDeCompta:
    """
    Le lundi, on ramasse la compta dans une dizaine de supermarchés de Paris pour
    tout livrer à la même adresse dans le 16e.

    Dans QuickDriver ce sont des courses indépendantes, facturées une par une,
    qui partagent seulement leur destination et leur label de tournée. Le moteur
    n'a donc rien de spécial à faire — mais il doit quand même les concentrer sur
    quelques coursiers plutôt que d'en donner une à chacun : celui qui va déjà
    dans le 16e y porte la deuxième pour presque rien.
    """

    SUPERMARCHES = [
        GpsPosition(lat=48.8809, lon=2.3553),   # 10e
        GpsPosition(lat=48.8483, lon=2.3958),   # 12e
        GpsPosition(lat=48.8533, lon=2.3692),   # 11e
        GpsPosition(lat=48.8422, lon=2.3220),   # 14e
        GpsPosition(lat=48.8709, lon=2.3317),   # 9e
        GpsPosition(lat=48.8864, lon=2.3432),   # 18e
        GpsPosition(lat=48.8312, lon=2.3555),   # 13e
        GpsPosition(lat=48.8583, lon=2.3470),   # 1er
    ]
    SEIZIEME = GpsPosition(lat=48.8710, lon=2.2830)

    def _flotte(self) -> FleetManager:
        fleet = FleetManager()
        for i, base in enumerate([DIX_SEPTIEME, DEUXIEME, BUREAU, CRETEIL]):
            fleet.add_coursier(Coursier(
                code=f"C{i}", vehicle_type=VehicleType.SCOOT_125, position=base,
            ))
        return fleet

    def _dispatcher_la_tournee(self):
        from app.services.comparaison import classer_coursiers
        fleet = self._flotte()
        attributions = []
        for i, depart in enumerate(self.SUPERMARCHES):
            course = commande(f"CPT{i}", depart, self.SEIZIEME)
            course.tournee = "compta-lundi-supermarches"
            retenu = next(c for c in classer_coursiers(course, fleet) if c.eligible)
            fleet.add_order(course)
            fleet.assign_order_to_coursier(course, retenu.code)
            attributions.append(retenu.code)
        return attributions

    def test_les_courses_se_concentrent_au_lieu_de_s_eparpiller(self) -> None:
        """Huit courses vers la même adresse ne doivent pas faire huit trajets vers le 16e."""
        attributions = self._dispatcher_la_tournee()
        assert len(set(attributions)) <= 3, f"éparpillées sur {set(attributions)}"

    def test_la_capacite_reste_respectee(self) -> None:
        """La concentration s'arrête à la sacoche : cinq unités, pas plus."""
        attributions = self._dispatcher_la_tournee()
        for code in set(attributions):
            assert attributions.count(code) <= 5

    def test_chaque_course_reste_independante(self) -> None:
        """
        Facturées une par une dans QuickDriver : le label groupe, il ne fusionne
        pas. Huit courses entrent, huit courses sont attribuées.
        """
        assert len(self._dispatcher_la_tournee()) == 8


# ═══════════════════════════════════════════════════════════════════════════
# Situation 5 — « commandé en voiture » n'oblige personne
# ═══════════════════════════════════════════════════════════════════════════

DIX_NEUVIEME = GpsPosition(lat=48.8817, lon=2.3822)
VINGTIEME_S5 = GpsPosition(lat=48.8656, lon=2.3958)


class TestCourseCommandeeEnVoiture:
    """
    « J'étais en route vers le 19e. Une course commandée EN VOITURE tombe, à
    ramasser dans le 20e — juste à côté. On me l'attribue. Je vais vérifier sur
    place si je peux prendre le carton moi-même : une voiture met beaucoup plus
    de temps qu'un scooter à traverser Paris, et coûte plus cher à faire rouler.
    Deux gros cartons, ça rentre dans ma caisse. Je préviens le dispatch que je
    la fais en scooter. »

    Ce que le client commande et ce que le colis pèse sont deux choses. La
    première se facture, la seconde décide de qui peut la porter. Les confondre
    exclurait tous les scooters d'une course qu'ils peuvent très bien faire.

    Le coursier ne touche à rien dans la fiche : il ramasse et valide, ou il
    appelle le dispatch si ça ne rentre pas.
    """

    def _en_route_vers_le_19e(self) -> Coursier:
        return Coursier(
            code="KEN", vehicle_type=VehicleType.SCOOT_125, position=DIX_NEUVIEME,
            assigned_orders=[course_portee("EN-COURS", DIX_NEUVIEME, DIX_SEPTIEME)],
        )

    def _commandee_en_voiture(self, volume: VolumeType) -> Order:
        c = commande("VOIT", VINGTIEME_S5, DIX_SEPTIEME)
        c.volume_type = volume
        c.vehicule_demande = VehicleType.VOITURE
        return c

    def test_un_scooter_prend_une_course_commandee_en_voiture(self) -> None:
        """Deux gros cartons qui rentrent dans la caisse : rien ne l'en empêche."""
        from app.services.dispatch import motif_inegibilite
        course = self._commandee_en_voiture(VolumeType.VOLUME)
        assert motif_inegibilite(self._en_route_vers_le_19e(), course) is None

    def test_la_demande_du_client_ne_penalise_pas_le_scooter(self) -> None:
        """
        Elle est indicative et facturée, pas contraignante. Le scooter ne doit
        pas être défavorisé pour l'avoir prise.
        """
        from app.services.dispatch import score_detail
        coursier = self._en_route_vers_le_19e()
        avec_demande = score_detail(coursier, self._commandee_en_voiture(VolumeType.VOLUME))

        sans = commande("VOIT", VINGTIEME_S5, DIX_SEPTIEME)
        sans.volume_type = VolumeType.VOLUME
        assert avec_demande.total == pytest.approx(score_detail(coursier, sans).total)

    def test_un_colis_reellement_trop_gros_reste_interdit(self) -> None:
        """
        La demande du client ne contraint pas, mais le volume réel, si. Un colis
        qui ne rentre pas dans la caisse ne rentre pas dans la caisse.
        """
        from app.services.dispatch import motif_inegibilite
        course = self._commandee_en_voiture(VolumeType.VOITURE)
        assert motif_inegibilite(self._en_route_vers_le_19e(), course) is not None

    def test_le_coursier_qui_passe_a_cote_l_emporte(self) -> None:
        """« On me l'attribue parce que j'étais en route pour aller juste à côté. »"""
        fleet = FleetManager()
        a_cote = self._en_route_vers_le_19e()
        loin = Coursier(code="LOI", vehicle_type=VehicleType.VOITURE, position=CRETEIL)
        fleet.add_coursier(a_cote)
        fleet.add_coursier(loin)
        fleet.add_order(commande("EN-COURS", DIX_NEUVIEME, DIX_SEPTIEME))

        course = self._commandee_en_voiture(VolumeType.VOLUME)
        assert score_coursier(a_cote, course, fleet) < score_coursier(loin, course, fleet)


class TestVoiturePlusLenteQueLeScooter:
    """
    « Une voiture met beaucoup plus de temps qu'un scooter à traverser Paris, et
    coûte plus cher à faire rouler. »
    """

    def test_la_voiture_est_la_plus_lente_apres_le_fourgon(self) -> None:
        from app.config import VITESSE_MOYENNE_KMH
        assert VITESSE_MOYENNE_KMH[VehicleType.VOITURE] < VITESSE_MOYENNE_KMH[VehicleType.SCOOT_50]
        assert VITESSE_MOYENNE_KMH[VehicleType.VOITURE] < VITESSE_MOYENNE_KMH[VehicleType.SCOOT_125]

    def test_a_position_egale_le_scooter_passe_devant(self) -> None:
        scoot = Coursier(code="SCO", vehicle_type=VehicleType.SCOOT_125, position=VINGTIEME_S5)
        auto = Coursier(code="AUT", vehicle_type=VehicleType.VOITURE, position=VINGTIEME_S5)
        course = commande("C", VINGTIEME_S5, DIX_SEPTIEME)
        course.volume_type = VolumeType.VOLUME
        assert score_coursier(scoot, course) < score_coursier(auto, course)
