"""Strategy per line-item type: validate the catalog row and snapshot its price."""

from crm.deals.exceptions import DealServiceError
from crm.deals.pricing import money


class LineItemStrategy:
    item_type = ""
    fk_attr = ""

    def resolve(self, company, data):
        raise NotImplementedError

    def _owned(self, company, obj, field):
        if obj is None or getattr(obj, "company_id", None) != company.id:
            raise DealServiceError(f"{field.replace('_', ' ').title()} was not found.", field=field)
        return obj


class ProductStrategy(LineItemStrategy):
    item_type = "product"
    fk_attr = "product"

    def resolve(self, company, data):
        product = self._owned(company, data.get("product"), "product")
        price = data.get("unit_price")
        if price is None:
            price = product.price
        return product.name, money(price), {"product": product}


class ServiceStrategy(LineItemStrategy):
    item_type = "service"
    fk_attr = "service"

    def resolve(self, company, data):
        service = self._owned(company, data.get("service"), "service")
        price = data.get("unit_price")
        if price is None:
            price = service.price
        return service.name, money(price), {"service": service}


class ServicePackageStrategy(LineItemStrategy):
    item_type = "service_package"
    fk_attr = "service_package"

    def resolve(self, company, data):
        package = self._owned(company, data.get("service_package"), "service_package")
        price = data.get("unit_price")
        if price is None:
            price = package.price
        return package.name, money(price), {"service_package": package}


class UnitStrategy(LineItemStrategy):
    item_type = "unit"
    fk_attr = "unit"

    def resolve(self, company, data):
        unit = self._owned(company, data.get("unit"), "unit")
        price = data.get("unit_price")
        if price is None:
            price = unit.price or 0
        name = unit.name or unit.code or "Unit"
        return name, money(price), {"unit": unit}


class CustomStrategy(LineItemStrategy):
    item_type = "custom"
    fk_attr = ""

    def resolve(self, company, data):
        name = (data.get("name") or "").strip()
        if not name:
            raise DealServiceError("Name is required.", field="name")
        if data.get("unit_price") is None:
            raise DealServiceError("Price is required.", field="unit_price")
        return name, money(data.get("unit_price")), {}


STRATEGIES = {
    strategy.item_type: strategy()
    for strategy in (
        ProductStrategy,
        ServiceStrategy,
        ServicePackageStrategy,
        UnitStrategy,
        CustomStrategy,
    )
}


def strategy_for(item_type):
    strategy = STRATEGIES.get(item_type)
    if strategy is None:
        raise DealServiceError("Unknown line item type.", field="item_type")
    return strategy
