"""
Gestionnaire d'état de la flotte (store en mémoire).

FleetManager est un singleton qui maintient :
- Le registre des coursiers (dict code → Courier)
- Le registre des commandes  (dict id   → Order)

En production, ces dicts seraient remplacés par des appels Redis/PostgreSQL.
L'interface publique reste identique pour faciliter cette migration.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.models.courier import Courier, AssignedOrder, GpsPosition
from app.models.order import Order
from app.models.enums import OrderStatus


class FleetManager:
    """
    Gestionnaire centralisé de l'état temps réel de la flotte.

    Usage :
        fleet = FleetManager()
        fleet.add_courier(courier)
        courier = fleet.get_courier("KEN")
    """

    def __init__(self) -> None:
        # Dictionnaire coursier_code → Courier
        self._couriers: Dict[str, Courier] = {}
        # Dictionnaire order_id → Order
        self._orders: Dict[str, Order] = {}

    # ------------------------------------------------------------------
    # Gestion des coursiers
    # ------------------------------------------------------------------

    def add_courier(self, courier: Courier) -> None:
        """
        Enregistre un nouveau coursier dans la flotte.

        Raises:
            ValueError: Si un coursier avec ce code existe déjà.
        """
        if courier.code in self._couriers:
            raise ValueError(f"Coursier '{courier.code}' déjà enregistré.")
        self._couriers[courier.code] = courier

    def get_courier(self, code: str) -> Optional[Courier]:
        """Retourne le coursier par son code 3 lettres, ou None."""
        return self._couriers.get(code.upper())

    def list_couriers(self) -> List[Courier]:
        """Retourne tous les coursiers enregistrés."""
        return list(self._couriers.values())

    def get_active_couriers(self) -> List[Courier]:
        """Retourne uniquement les coursiers actifs (connectés et disponibles)."""
        return [c for c in self._couriers.values() if c.is_active]

    def update_courier_position(self, code: str, lat: float, lon: float) -> Courier:
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
        courier = self._couriers.get(code.upper())
        if courier is None:
            raise KeyError(f"Coursier '{code}' introuvable.")
        courier.position = GpsPosition(lat=lat, lon=lon)
        return courier

    def assign_order_to_courier(self, order: Order, courier_code: str) -> None:
        """
        Attribue une commande à un coursier :
        - Ajoute un AssignedOrder dans la liste du coursier
        - Met à jour le statut et le champ assigned_courier de la commande

        Args:
            order        : La commande à attribuer.
            courier_code : Code du coursier destinataire.

        Raises:
            KeyError: Si le coursier est introuvable.
        """
        courier = self._couriers.get(courier_code.upper())
        if courier is None:
            raise KeyError(f"Coursier '{courier_code}' introuvable.")

        # Crée le snapshot léger pour le coursier
        assigned = AssignedOrder(
            order_id=order.id,
            pickup_lat=order.pickup.lat,
            pickup_lon=order.pickup.lon,
            delivery_lat=order.delivery.lat,
            delivery_lon=order.delivery.lon,
            volume_type=order.volume_type,
        )
        courier.assigned_orders.append(assigned)

        # Met à jour la commande
        order.status = OrderStatus.ASSIGNED
        order.assigned_courier = courier_code.upper()

    def remove_order_from_courier(self, order_id: str, courier_code: str) -> None:
        """
        Retire une commande du portefeuille d'un coursier (après livraison ou annulation).

        Args:
            order_id     : Identifiant de la commande à retirer.
            courier_code : Code du coursier concerné.
        """
        courier = self._couriers.get(courier_code.upper())
        if courier is None:
            return
        courier.assigned_orders = [o for o in courier.assigned_orders if o.order_id != order_id]

    def set_courier_active(self, code: str, active: bool) -> None:
        """Active ou désactive un coursier (ex: fin de service, panne)."""
        courier = self._couriers.get(code.upper())
        if courier:
            courier.is_active = active

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
        self._couriers.clear()
        self._orders.clear()

    @property
    def courier_count(self) -> int:
        return len(self._couriers)

    @property
    def order_count(self) -> int:
        return len(self._orders)


# Instance globale partagée par toute l'application
# (remplacer par un système d'injection de dépendances si multi-tenant)
fleet_manager = FleetManager()
