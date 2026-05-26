"""
Gestionnaire d'état de la flotte (store en mémoire).

FleetManager est un singleton qui maintient :
- Le registre des coursiers (dict code → Coursier)
- Le registre des commandes  (dict id   → Order)

En production, ces dicts seraient remplacés par des appels Redis/PostgreSQL.
L'interface publique reste identique pour faciliter cette migration.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.models.coursier import Coursier, AssignedOrder, GpsPosition
from app.models.order import Order
from app.models.enums import OrderStatus


class FleetManager:
    """
    Gestionnaire centralisé de l'état temps réel de la flotte.

    Usage :
        fleet = FleetManager()
        fleet.add_coursier(coursier)
        coursier = fleet.get_coursier("KEN")
    """

    def __init__(self) -> None:
        # Dictionnaire coursier_code → Coursier
        self._coursiers: Dict[str, Coursier] = {}
        # Dictionnaire order_id → Order
        self._orders: Dict[str, Order] = {}

    # ------------------------------------------------------------------
    # Gestion des coursiers
    # ------------------------------------------------------------------

    def add_coursier(self, coursier: Coursier) -> None:
        """
        Enregistre un nouveau coursier dans la flotte.

        Raises:
            ValueError: Si un coursier avec ce code existe déjà.
        """
        if coursier.code in self._coursiers:
            raise ValueError(f"Coursier '{coursier.code}' déjà enregistré.")
        self._coursiers[coursier.code] = coursier

    def get_coursier(self, code: str) -> Optional[Coursier]:
        """Retourne le coursier par son code 3 lettres, ou None."""
        return self._coursiers.get(code.upper())

    def list_coursiers(self) -> List[Coursier]:
        """Retourne tous les coursiers enregistrés."""
        return list(self._coursiers.values())

    def get_active_coursiers(self) -> List[Coursier]:
        """Retourne uniquement les coursiers actifs (connectés et disponibles)."""
        return [c for c in self._coursiers.values() if c.is_active]

    def update_coursier_position(self, code: str, lat: float, lon: float) -> Coursier:
        """
        Met à jour la position GPS d'un coursier en temps réel.

        Args:
            code : Code 3 lettres du coursier.
            lat  : Nouvelle latitude.
            lon  : Nouvelle longitude.

        Returns:
            Le coursier mis à jour.

        Raises:
            KeyError: Si le code est inconnu.
        """
        coursier = self._coursiers.get(code.upper())
        if coursier is None:
            raise KeyError(f"Coursier '{code}' introuvable.")
        coursier.position = GpsPosition(lat=lat, lon=lon)
        return coursier

    def assign_order_to_coursier(self, order: Order, coursier_code: str) -> None:
        """
        Attribue une commande à un coursier :
        - Ajoute un AssignedOrder dans la liste du coursier
        - Met à jour le statut et le champ assigned_coursier de la commande

        Args:
            order        : La commande à attribuer.
            coursier_code : Code du coursier destinataire.

        Raises:
            KeyError: Si le coursier est introuvable.
        """
        coursier = self._coursiers.get(coursier_code.upper())
        if coursier is None:
            raise KeyError(f"Coursier '{coursier_code}' introuvable.")

        # Crée le snapshot léger pour le coursier
        assigned = AssignedOrder(
            order_id=order.id,
            pickup_lat=order.pickup.lat,
            pickup_lon=order.pickup.lon,
            delivery_lat=order.delivery.lat,
            delivery_lon=order.delivery.lon,
            volume_type=order.volume_type,
        )
        coursier.assigned_orders.append(assigned)

        # Met à jour la commande
        order.status = OrderStatus.ASSIGNED
        order.assigned_coursier = coursier_code.upper()

    def remove_order_from_coursier(self, order_id: str, coursier_code: str) -> None:
        """
        Retire une commande du portefeuille d'un coursier (après livraison ou annulation).

        Args:
            order_id     : Identifiant de la commande à retirer.
            coursier_code : Code du coursier concerné.
        """
        coursier = self._coursiers.get(coursier_code.upper())
        if coursier is None:
            return
        coursier.assigned_orders = [o for o in coursier.assigned_orders if o.order_id != order_id]

    def set_coursier_active(self, code: str, active: bool) -> None:
        """Active ou désactive un coursier (ex: fin de service, panne)."""
        coursier = self._coursiers.get(code.upper())
        if coursier:
            coursier.is_active = active

    def update_coursier(
        self,
        code: str,
        *,
        vehicle_type=None,
        lat: float | None = None,
        lon: float | None = None,
        is_active: bool | None = None,
    ) -> Coursier:
        """
        Met à jour un ou plusieurs champs d'un coursier en une seule opération.

        Args:
            code         : Code 3 lettres du coursier.
            vehicle_type : Nouveau type de véhicule (None = inchangé).
            lat / lon    : Nouvelle position GPS (les deux requis pour bouger).
            is_active    : Nouveau statut actif (None = inchangé).

        Returns:
            Le coursier mis à jour.

        Raises:
            KeyError: Si le code est inconnu.
        """
        coursier = self._coursiers.get(code.upper())
        if coursier is None:
            raise KeyError(f"Coursier '{code.upper()}' introuvable.")
        if vehicle_type is not None:
            coursier.vehicle_type = vehicle_type
        if lat is not None and lon is not None:
            coursier.position = GpsPosition(lat=lat, lon=lon)
        if is_active is not None:
            coursier.is_active = is_active
        return coursier

    # ------------------------------------------------------------------
    # Gestion des commandes
    # ------------------------------------------------------------------

    def add_order(self, order: Order) -> None:
        """Enregistre une nouvelle commande dans le store."""
        self._orders[order.id] = order

    def get_order(self, order_id: str) -> Optional[Order]:
        """Retourne une commande par son ID, ou None."""
        return self._orders.get(order_id)

    def list_orders(self) -> List[Order]:
        """Retourne toutes les commandes enregistrées."""
        return list(self._orders.values())

    # ------------------------------------------------------------------
    # Statistiques
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Vide complètement le store — coursiers ET commandes. Utile pour la démo."""
        self._coursiers.clear()
        self._orders.clear()

    @property
    def coursier_count(self) -> int:
        return len(self._coursiers)

    @property
    def order_count(self) -> int:
        return len(self._orders)


# Instance globale partagée par toute l'application
# (remplacer par un système d'injection de dépendances si multi-tenant)
fleet_manager = FleetManager()
