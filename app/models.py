from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(120))
    # 4-значный PIN — необязательный «быстрый вход» поверх пароля. Пусто = PIN не задан.
    pin_hash: Mapped[str] = mapped_column(String(120), default="")
    display_name: Mapped[str] = mapped_column(String(120), default="")

    # Привязка Telegram: пользователь на сайте выпускает link_code, отправляет
    # его боту /start <code>, бот сохраняет tg_user_id — дальше рецепты из
    # бота ложатся в книгу этого пользователя.
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    tg_link_code: Mapped[str] = mapped_column(String(20), default="", index=True)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    recipes: Mapped[list["Recipe"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan")


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(220), index=True)
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str] = mapped_column(Text, default="")

    source_url: Mapped[str] = mapped_column(Text, default="")
    source_key: Mapped[str] = mapped_column(String(64), index=True, default="")
    source_domain: Mapped[str] = mapped_column(String(160), default="")
    source_title: Mapped[str] = mapped_column(String(300), default="")

    category: Mapped[str] = mapped_column(String(40), index=True, default="main")
    category_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    category_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    servings: Mapped[str] = mapped_column(String(60), default="")
    total_minutes: Mapped[int] = mapped_column(Integer, default=0)

    parse_method: Mapped[str] = mapped_column(String(30), default="")
    added_from: Mapped[str] = mapped_column(String(20), default="web")
    added_by: Mapped[str] = mapped_column(String(120), default="")
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)

    search_blob: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    owner: Mapped["User"] = relationship(back_populates="recipes")
    ingredients: Mapped[list["Ingredient"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan",
        order_by="Ingredient.position", lazy="selectin")
    steps: Mapped[list["Step"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan",
        order_by="Step.position", lazy="selectin")
    images: Mapped[list["Image"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan",
        order_by="Image.position", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("owner_id", "slug", name="uq_recipes_owner_slug"),
        UniqueConstraint("owner_id", "source_key", name="uq_recipes_owner_source"),
    )

    @property
    def cover(self):
        for img in self.images:
            if not img.is_source:
                return img
        return None

    @property
    def source_photo(self):
        for img in self.images:
            if img.is_source:
                return img
        return None

    @property
    def gallery(self) -> list:
        dish = [img for img in self.images if not img.is_source]
        return dish[1:] if len(dish) > 1 else []

    @property
    def time_label(self) -> str:
        m = self.total_minutes
        if not m:
            return ""
        if m < 60:
            return f"{m} мин"
        h, rest = divmod(m, 60)
        return f"{h} ч" if not rest else f"{h} ч {rest} мин"


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    raw: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(200), default="")
    name_norm: Mapped[str] = mapped_column(String(200), index=True, default="")
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(String(200), default="")

    recipe: Mapped["Recipe"] = relationship(back_populates="ingredients")


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)

    recipe: Mapped["Recipe"] = relationship(back_populates="steps")


class Image(Base):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    filename: Mapped[str] = mapped_column(String(160))
    thumb_filename: Mapped[str] = mapped_column(String(160))
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    source_url: Mapped[str] = mapped_column(Text, default="")
    # true, если это фото-источник (страница книги, скриншот),
    # а не картинка блюда — такие не берутся в обложку и в галерею
    is_source: Mapped[bool] = mapped_column(Boolean, default=False)

    recipe: Mapped["Recipe"] = relationship(back_populates="images")


Index("ix_recipes_owner_created", Recipe.owner_id, Recipe.created_at)
Index("ix_recipes_owner_category", Recipe.owner_id, Recipe.category)
