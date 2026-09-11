"""Seed script to populate the database with initial menu data and tables."""
import sys
import os

# Ensure the app module is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database.connection import engine, SessionLocal, Base
from app.models import (
    MenuCategory, MenuItem, RestaurantTable, Customer, Order, OrderItem, Payment, User
)

def seed():
    # Create all tables
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully.")

    db = SessionLocal()
    try:
        # Check if data already exists
        existing = db.query(MenuCategory).first()
        if existing:
            print("Database already seeded. Skipping.")
            return

        # --- Categories ---
        categories = {
            "food": MenuCategory(name="food", description="Delicious Ghanaian and international food"),
            "beer": MenuCategory(name="beer", description="Ice-cold beers"),
            "cocktails": MenuCategory(name="cocktails", description="Signature cocktails"),
            "soft-drinks": MenuCategory(name="soft-drinks", description="Soft drinks and water"),
        }
        for cat in categories.values():
            db.add(cat)
        db.flush()  # Get IDs assigned

        print(f"Created {len(categories)} categories.")

        # --- Menu Items ---
        menu_items = [
            # FOOD
            MenuItem(name="Ghanaian Jollof Rice", description="Authentic firewood-smoked Ghanaian jollof rice served with spiced grilled chicken quarter and shito.", price=50, category_id=categories["food"].id, is_available=True),
            MenuItem(name="Republic Special Fried Rice", description="Wok-tossed seasoned basmati rice with spring vegetables, egg ribbons, and charcoal grilled chicken.", price=50, category_id=categories["food"].id, is_available=True),
            MenuItem(name="Charcoal Grilled Chicken", description="Succulent quarter chicken basted in roasted ginger, garlic, and wild Ghanaian chili rub.", price=45, category_id=categories["food"].id, is_available=True),
            MenuItem(name="Crispy Peppered Chicken Wings", description="Crispy fried wings tossed in our signature smoky Scotch Bonnet honey glaze. 6 pieces.", price=50, category_id=categories["food"].id, is_available=True),
            MenuItem(name="Golden French Fries", description="Crispy seasoned double-fried potato fries served with spiced house aioli.", price=30, category_id=categories["food"].id, is_available=True),
            MenuItem(name="Spicy Kelewele", description="Ripe plantain cubes seasoned with ground ginger, cayenne pepper, and cloves, fried golden brown.", price=35, category_id=categories["food"].id, is_available=True),
            MenuItem(name="Beef Suya Skewers", description="Tender beef skewers crusted with Northern Ghanaian Yaji spice, served with sliced onions and fresh tomatoes.", price=55, category_id=categories["food"].id, is_available=True),
            # BEER
            MenuItem(name="Club Beer (Large)", description="Chilled 625ml bottle of Ghana's iconic lager. Crisp, golden, and refreshing.", price=25, category_id=categories["beer"].id, is_available=True),
            MenuItem(name="Guinness Extra Stout", description="Smooth, bold foreign extra stout with rich roasted barley notes. Served ice cold.", price=30, category_id=categories["beer"].id, is_available=True),
            MenuItem(name="Stella Artois", description="Crisp European premium pilsner lager served in an embossed glass.", price=35, category_id=categories["beer"].id, is_available=True),
            # COCKTAILS
            MenuItem(name="Classic Republic Mojito", description="White rum, freshly crushed garden mint, zesty lime wedges, simple syrup, and sparkling club soda.", price=50, category_id=categories["cocktails"].id, is_available=True),
            MenuItem(name="Accra Passion Fruit Cocktail", description="Vodka infused with fresh Ghanaian passion fruit pulp, triple sec, citrus blend, and crushed ice.", price=55, category_id=categories["cocktails"].id, is_available=True),
            MenuItem(name="The Star Experience Gold Mule", description="Spiced dark rum, fresh fiery ginger beer, lime juice, dash of Angostura, dusted with edible gold glitter.", price=60, category_id=categories["cocktails"].id, is_available=True),
            MenuItem(name="Hibiscus \"Sobolo\" Rum Punch", description="Local spiced hibiscus infusion mixed with dark Jamaican rum, pineapple juice, and cinnamon essence.", price=55, category_id=categories["cocktails"].id, is_available=True),
            # SOFT DRINKS
            MenuItem(name="Coca-Cola (Glass Bottle)", description="Ice-cold classic 330ml Coca-Cola served with lime wedge.", price=15, category_id=categories["soft-drinks"].id, is_available=True),
            MenuItem(name="Sprite (Glass Bottle)", description="Refreshing lemon-lime crisp soda served chilled.", price=15, category_id=categories["soft-drinks"].id, is_available=True),
            MenuItem(name="Fanta Orange", description="Sweet and bubbly orange soda served over ice.", price=15, category_id=categories["soft-drinks"].id, is_available=True),
            MenuItem(name="Bel-Aqua Natural Mineral Water", description="Chilled 750ml still natural mineral spring water.", price=10, category_id=categories["soft-drinks"].id, is_available=True),
        ]
        for item in menu_items:
            db.add(item)
        print(f"Created {len(menu_items)} menu items.")

        # --- Restaurant Tables ---
        tables = []
        for i in range(1, 21):
            tables.append(RestaurantTable(table_number=str(i), capacity=4))
        for t in tables:
            db.add(t)
        print(f"Created {len(tables)} restaurant tables.")

        db.commit()
        print("\nDatabase seeded successfully!")

    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed()
