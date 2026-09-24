"""sales_data ORM 实体的兼容导出入口。"""

from salesmind.db.entities.sales_data_dimensions import Product, SalesRegion, SalesRep
from salesmind.db.entities.sales_data_orders import SalesOrder
from salesmind.db.entities.sales_data_versions import DatasetVersion

__all__ = ["DatasetVersion", "Product", "SalesOrder", "SalesRegion", "SalesRep"]
