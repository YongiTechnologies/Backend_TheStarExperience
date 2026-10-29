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
        operational_rows = {
            "orders": db.query(Order).count(),
            "order_items": db.query(OrderItem).count(),
            "payments": db.query(Payment).count(),
        }
        if any(operational_rows.values()):
            raise RuntimeError(
                f"Refusing to replace the menu while operational records exist: {operational_rows}"
            )

        category_descriptions = {
            "food": "Republic Bar food menu: main dishes, vegetarian options and sides.",
            "beer": "Republic Bar beers, ciders and wild beer mixes.",
            "cocktails": "Republic Bar cane-spirit cocktails; menu cocktails are 1 litre where specified.",
            "mocktails": "Republic Bar non-alcoholic drinks and mocktails.",
            "wine": "Republic Bar wines and palm wine.",
            "spirits": "Akpeteshie-cane spirit shots and infused bottles.",
            "soft-drinks": "Republic Bar natural juices, sodas, water and soft drinks.",
        }
        categories = {}
        for name, description in category_descriptions.items():
            category = db.query(MenuCategory).filter(MenuCategory.name == name).first()
            if category is None:
                category = MenuCategory(name=name, description=description)
                db.add(category)
            else:
                category.description = description
            categories[name] = category
        db.flush()

        db.query(MenuItem).delete(synchronize_session=False)
        db.query(MenuCategory).filter(
            ~MenuCategory.name.in_(category_descriptions)
        ).delete(synchronize_session=False)

        catalog = [
            ("food", "Gogo Balls", "Spiced beef balls minced with cumin, black pepper, crumbs, onion and garlic.", 120),
            ("food", "Yeye Goat", "Goat curry in mild spices, green peppers and spices. Some bones attached.", 100),
            ("food", "Yeye Chicken", "Chicken curry in mild curry sauce with chunks of boiled potato.", 120),
            ("food", "Chicken Call", "Spicy oven-grilled chicken wings.", 95),
            ("food", "Domedo Grilled", "Pan-grilled pork marinated and steamed in local indigenous spices.", 95),
            ("food", "Calamari Squid", "Fried squid slices in oregano and paprika.", 150),
            ("food", "Fried Veg Home Grown", "Brown Ghanaian organic rice with specks of beans, fried with assorted vegetables. Choose coconut oil or vegetable oil.", 80),
            ("food", "Virgin Green Revolution", "Avocado mixed with spring onions, coriander and lime juice dressing.", 80),
            ("food", "Stir Fried Vegetables", "Mixed fresh vegetables stir-fried with a dash of soya sauce.", 80),
            ("food", "Egg Fried Rice (Brown Rice)", "Brown Ghanaian organic rice with specks of beans, egg and mixed fresh vegetables, stir-fried. Choose coconut oil or vegetable oil.", 80),
            ("food", "Special Home Grown", "Choose small cuts of chicken, beef balls or grilled pork, fried with brown rice and vegetables.", 100),
            ("food", "Green Revolution", "Avocado mixed with tuna, spring onions, coriander and lime juice dressing.", 100),
            ("food", "Mixed Grilled Platter", "Six beef meatballs, fried calamari (300 g), chicken wings (250 g), grilled pork (250 g), stir-fried home-grown brown rice, cassava chips and fried yam.", 500),
            ("food", "Jollof Rice", "Rice, oil, onion, pepper, fresh tomatoes, tomato paste and green bell pepper.", 70),
            ("food", "Yamrock", "Slender-cut pieces of yam fried in deep vegetable oil.", 40),
            ("food", "Cassava Havana", "Boiled cassava cut into slender pieces and fried in deep vegetable oil.", 40),
            ("food", "Kelewele", "Slender-cut pieces of plantain fried in deep vegetable oil.", 40),
            ("cocktails", "Kokoroko (House Favourite)", "Pulverized ice, spirit shot, hibiscus, lime, mint and brown sugar. 1 litre.", 100),
            ("cocktails", "Passion Kokoroko", "Passion fruit, lime, crushed ice, cane spirit and brown sugar. 1 litre.", 100),
            ("cocktails", "Mint Kokoroko", "Mint, lime, crushed ice, cane spirit and brown sugar. 1 litre.", 100),
            ("cocktails", "Frozen Harmattan", "Pulverized ice, coconut shot, lime juice, brown sugar and mint garnish. 1 litre.", 100),
            ("cocktails", "Republica", "Caipirinha-style drink with lime, crushed ice, cane spirit and sugar. 1 litre.", 100),
            ("cocktails", "Kukuruntumi", "Vanilla-cinnamon spirit, coconut spirit, ice, lime and Coca-Cola. 1 litre.", 100),
            ("cocktails", "Woezor", "Lime, crushed ice, coconut cane spirit, sugar and a splash of pineapple juice. 1 litre.", 100),
            ("cocktails", "Mojito", "Mint leaves, soda water, sugar, cane spirit, crushed ice and lime. 1 litre.", 100),
            ("mocktails", "Ghana", "Pulverized ice, hibiscus, lime, mint, passion fruit and brown sugar. 1 litre. Available at the East Legon Substation only.", 80),
            ("mocktails", "Lyngwa", "Mint, lime, bissap and ginger concentrate, soda water, ice and pineapple juice. 1 litre.", 100),
            ("mocktails", "Lemonade Mocktail", "Lime, soda water, ice and sugar.", 60),
            ("wine", "House Red or White (Glass)", "House red or white wine, by the glass.", 90),
            ("wine", "House Red or White (Bottle)", "House red or white wine, by the bottle.", 380),
            ("wine", "Palm Wine", "Local wine served fresh from the palm tree plantation.", 50),
            ("spirits", "Akpeteshie-Cane Spirit Shot (Ginger)", "Ginger-infused Akpeteshie cane spirit shot.", 50),
            ("spirits", "Akpeteshie-Cane Spirit Shot (Vanilla)", "Vanilla-infused Akpeteshie cane spirit shot.", 50),
            ("spirits", "Akpeteshie-Cane Spirit Shot (Coffee-Cocoa)", "Coffee-cocoa-infused Akpeteshie cane spirit shot.", 50),
            ("spirits", "Akpeteshie-Cane Spirit Shot (Coconut)", "Coconut-infused Akpeteshie cane spirit shot.", 50),
            ("spirits", "Akpeteshie-Cane Spirit Shot (Passion Fruit)", "Passion-fruit-infused Akpeteshie cane spirit shot.", 50),
            ("spirits", "Akpeteshie Spirit Bottle (Ginger, 500 ml)", "Ginger-infused Akpeteshie cane spirit. 500 ml bottle.", 400),
            ("spirits", "Akpeteshie Spirit Bottle (Vanilla & Cinnamon, 500 ml)", "Vanilla and cinnamon-infused Akpeteshie cane spirit. 500 ml bottle.", 400),
            ("spirits", "Akpeteshie Spirit Bottle (Coffee-Cocoa, 500 ml)", "Coffee-cocoa-infused Akpeteshie cane spirit. 500 ml bottle.", 400),
            ("spirits", "Akpeteshie Spirit Bottle (Coconut & Vanilla, 500 ml)", "Coconut and vanilla-infused Akpeteshie cane spirit. 500 ml bottle.", 400),
            ("spirits", "Akpeteshie Spirit Bottle (Passion Fruit, 500 ml)", "Passion-fruit-infused Akpeteshie cane spirit. 500 ml bottle.", 400),
            ("spirits", "Akpeteshie Spirit Bottle (Ginger, 1 L)", "Ginger-infused Akpeteshie cane spirit. 1 litre bottle.", 700),
            ("spirits", "Akpeteshie Spirit Bottle (Vanilla & Cinnamon, 1 L)", "Vanilla and cinnamon-infused Akpeteshie cane spirit. 1 litre bottle.", 700),
            ("spirits", "Akpeteshie Spirit Bottle (Coffee-Cocoa, 1 L)", "Coffee-cocoa-infused Akpeteshie cane spirit. 1 litre bottle.", 700),
            ("spirits", "Akpeteshie Spirit Bottle (Coconut & Vanilla, 1 L)", "Coconut and vanilla-infused Akpeteshie cane spirit. 1 litre bottle.", 700),
            ("spirits", "Akpeteshie Spirit Bottle (Passion Fruit, 1 L)", "Passion-fruit-infused Akpeteshie cane spirit. 1 litre bottle.", 700),
            ("beer", "Beer Sop", "Club beer with bissap concentrate.", 50),
            ("beer", "Ginger Beer", "Club beer with ginger concentrate.", 50),
            ("beer", "Sunshine Beer", "Club beer with a lime dash and salt.", 50),
            ("beer", "Star / Club (Small)", "Star or Club beer, small size.", 40),
            ("beer", "Star / Club (Large)", "Star or Club beer, large size.", 60),
            ("beer", "Smirnoff Ice", "Smirnoff Ice.", 50),
            ("beer", "Smirnoff Black", "Smirnoff Black.", 50),
            ("beer", "Guinness", "Guinness stout.", 40),
            ("beer", "Savannah Dry Cider", "Savannah Dry cider.", 65),
            ("beer", "Hunters Gold Cider", "Hunters Gold cider.", 65),
            ("beer", "Eagle Beer", "Eagle beer.", 35),
            ("beer", "Tale Beer", "Tale beer.", 35),
            ("soft-drinks", "Hibiscus Juice (Bissap)", "Natural hibiscus juice, also known as bissap.", 45),
            ("soft-drinks", "Pineapple Juice", "Natural pineapple juice.", 50),
            ("soft-drinks", "Soursop Iced Tea", "Soursop iced tea sweetened with honey and spices.", 50),
            ("soft-drinks", "Orange Juice", "Pure orange juice.", 50),
            ("soft-drinks", "House Lemonade", "House lemonade.", 50),
            ("soft-drinks", "Coke", "Coca-Cola.", 30),
            ("soft-drinks", "Fanta", "Fanta.", 30),
            ("soft-drinks", "Sprite", "Sprite.", 30),
            ("soft-drinks", "Alvaro", "Alvaro.", 30),
            ("soft-drinks", "Water", "Bottled water.", 15),
            ("soft-drinks", "Malta Guinness", "Malta Guinness.", 35),
            ("soft-drinks", "Soda Water", "Soda water.", 30),
        ]
        for category_name, name, description, price in catalog:
            db.add(MenuItem(
                name=name,
                description=description,
                price=price,
                category_id=categories[category_name].id,
                is_available=not (category_name == "mocktails" and name == "Ghana"),
            ))
        print(f"Created {len(category_descriptions)} categories and {len(catalog)} menu items.")

        # --- Restaurant Tables ---
        if db.query(RestaurantTable).count() == 0:
            tables = [RestaurantTable(table_number=str(i), capacity=4) for i in range(1, 21)]
            db.add_all(tables)
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
