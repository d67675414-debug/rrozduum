from datetime import datetime, timedelta
import os
import re
import uuid

from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_login import (
    LoginManager, UserMixin, current_user,
    login_user, logout_user, login_required
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

basedir = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = "rozdum_secret_key_change_me"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "forum.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(basedir, "static", "uploads")

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

ADMIN_PASSWORD = "141022"
REACTIONS = ["🔥", "❤️", "😂", "😎", "👍", "👏"]
ONLINE_WINDOW = timedelta(minutes=5)
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    nickname = db.Column(db.String(80), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    avatar = db.Column(db.String(255), default="")
    bio = db.Column(db.Text, default="")
    status = db.Column(db.String(120), default="")
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Topic(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(220), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    author = db.relationship("User", backref=db.backref("topics", lazy=True))


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("comment.id"), nullable=True)

    author = db.relationship("User", backref=db.backref("comments", lazy=True))
    topic = db.relationship("Topic", backref=db.backref("all_comments", lazy=True))
    parent = db.relationship("Comment", remote_side=[id], backref=db.backref("replies", lazy=True))


class Reaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reaction = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    topic_id = db.Column(db.Integer, db.ForeignKey("topic.id"), nullable=False)

    user = db.relationship("User", backref=db.backref("reactions", lazy=True))
    topic = db.relationship("Topic", backref=db.backref("reactions", lazy=True))


class CommentReaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reaction = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    comment_id = db.Column(db.Integer, db.ForeignKey("comment.id"), nullable=False)

    user = db.relationship("User", backref=db.backref("comment_reactions", lazy=True))
    comment = db.relationship("Comment", backref=db.backref("reactions", lazy=True))


class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    follower_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    following_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    follower = db.relationship("User", foreign_keys=[follower_id], backref=db.backref("following", lazy=True))
    following = db.relationship("User", foreign_keys=[following_id], backref=db.backref("followers_rel", lazy=True))


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(500), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    user = db.relationship("User", backref=db.backref("notifications", lazy=True))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def is_online(user):
    if not user or not user.last_seen:
        return False
    return datetime.utcnow() - user.last_seen <= ONLINE_WINDOW


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_uploaded_image(file_storage):
    if not file_storage or not file_storage.filename:
        return ""

    if not allowed_image(file_storage.filename):
        return None

    filename = secure_filename(file_storage.filename)
    unique_name = f"{uuid.uuid4()}_{filename}"
    path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file_storage.save(path)
    return unique_name


def delete_uploaded_image(filename):
    if not filename:
        return
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    if os.path.exists(path):
        os.remove(path)


def make_notification(user_id, text, link):
    db.session.add(Notification(user_id=user_id, text=text, link=link))


def decorate_topic(topic):
    topic.reaction_counts = {
        emoji: Reaction.query.filter_by(topic_id=topic.id, reaction=emoji).count()
        for emoji in REACTIONS
    }
    topic.comment_count = Comment.query.filter_by(topic_id=topic.id, parent_id=None).count()

    if current_user.is_authenticated:
        my_reaction = Reaction.query.filter_by(topic_id=topic.id, user_id=current_user.id).first()
        topic.my_reaction = my_reaction.reaction if my_reaction else ""
    else:
        topic.my_reaction = ""

    return topic


def decorate_comment(comment):
    comment.reaction_counts = {
        emoji: CommentReaction.query.filter_by(comment_id=comment.id, reaction=emoji).count()
        for emoji in REACTIONS
    }

    if current_user.is_authenticated:
        my_reaction = CommentReaction.query.filter_by(comment_id=comment.id, user_id=current_user.id).first()
        comment.my_reaction = my_reaction.reaction if my_reaction else ""
    else:
        comment.my_reaction = ""

    for reply in comment.replies:
        decorate_comment(reply)

    return comment


def can_manage_topic(topic):
    return current_user.is_authenticated and (current_user.is_admin or topic.user_id == current_user.id)


def can_manage_comment(comment):
    return current_user.is_authenticated and (current_user.is_admin or comment.user_id == current_user.id)


def delete_comment_tree(comment):
    for reply in list(comment.replies):
        delete_comment_tree(reply)

    CommentReaction.query.filter_by(comment_id=comment.id).delete(synchronize_session=False)
    db.session.delete(comment)


@app.context_processor
def inject_globals():
    unread = 0
    if current_user.is_authenticated:
        unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return {
        "unread_notifications_count": unread,
        "reactions_list": REACTIONS,
        "is_online": is_online,
    }


@app.before_request
def update_last_seen():
    if request.endpoint == "static":
        return
    if current_user.is_authenticated:
        if current_user.is_banned:
            logout_user()
            flash("Ваш аккаунт заблокирован.")
            return redirect(url_for("login"))
        current_user.last_seen = datetime.utcnow()
        db.session.commit()


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("forum"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        nickname = request.form.get("nickname", "").strip()
        password_text = request.form.get("password", "")

        if not username or not nickname or not password_text:
            flash("Заполни все поля.")
            return render_template("register.html")

        if not re.fullmatch(r"[a-zA-Z0-9_]+", username):
            flash("Логин должен быть только на английском: буквы, цифры и _")
            return render_template("register.html")

        if User.query.filter_by(username=username).first():
            flash("Такой логин уже занят.")
            return render_template("register.html")

        avatar_file = request.files.get("avatar")
        avatar_name = save_uploaded_image(avatar_file)
        if avatar_name is None:
            flash("Аватарка должна быть изображением.")
            return render_template("register.html")

        user = User(
            username=username,
            nickname=nickname,
            password=generate_password_hash(password_text),
            avatar=avatar_name,
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Аккаунт создан.")
        return redirect(url_for("forum"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password_text = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password, password_text):
            flash("Неверный логин или пароль.")
            return render_template("login.html")

        if user.is_banned:
            flash("Этот аккаунт заблокирован.")
            return render_template("login.html")

        login_user(user)
        flash("Добро пожаловать в ROZDUM.")
        return redirect(url_for("forum"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Вы вышли из аккаунта.")
    return redirect(url_for("login"))


@app.route("/admin/login", methods=["POST"])
@login_required
def admin_login():
    password_text = request.form.get("password", "")
    if password_text == ADMIN_PASSWORD:
        current_user.is_admin = True
        db.session.commit()
        flash("Админ-режим включён.")
    else:
        flash("Неверный код.")
    return redirect(request.referrer or url_for("forum"))


@app.route("/forum")
@login_required
def forum():
    search = request.args.get("q", "").strip()

    query = Topic.query.join(User, Topic.user_id == User.id)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Topic.title.ilike(like),
                Topic.content.ilike(like),
                User.nickname.ilike(like),
                User.username.ilike(like),
            )
        )

    topics = query.order_by(Topic.id.desc()).all()
    for topic in topics:
        decorate_topic(topic)

    return render_template(
        "forum.html",
        topics=topics,
        search=search,
        total_topics=Topic.query.count(),
        total_users=User.query.count(),
        total_comments=Comment.query.count(),
    )


@app.route("/topic/new", methods=["GET", "POST"])
@login_required
def create_topic():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()

        if not title or not content:
            flash("Заполни заголовок и текст.")
            return render_template("create.html")

        image_file = request.files.get("image")
        image_name = save_uploaded_image(image_file)
        if image_name is None:
            flash("Фото должно быть изображением.")
            return render_template("create.html")

        topic = Topic(
            title=title,
            content=content,
            image=image_name,
            user_id=current_user.id,
        )
        db.session.add(topic)
        db.session.commit()
        flash("Пост опубликован.")
        return redirect(url_for("forum"))

    return render_template("create.html")


@app.route("/topic/<int:topic_id>")
@login_required
def topic_view(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    decorate_topic(topic)
    comments = Comment.query.filter_by(topic_id=topic.id, parent_id=None).order_by(Comment.id.asc()).all()
    for comment in comments:
        decorate_comment(comment)
    return render_template("topic.html", topic=topic, comments=comments)


@app.route("/topic/<int:topic_id>/react", methods=["POST"])
@login_required
def react_topic(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    reaction = request.form.get("reaction", "").strip()

    if reaction not in REACTIONS:
        abort(400)

    existing = Reaction.query.filter_by(topic_id=topic.id, user_id=current_user.id).first()
    if existing:
        if existing.reaction == reaction:
            db.session.delete(existing)
            flash("Реакция убрана.")
        else:
            existing.reaction = reaction
            flash("Реакция изменена.")
    else:
        db.session.add(Reaction(topic_id=topic.id, user_id=current_user.id, reaction=reaction))
        flash("Реакция добавлена.")

    if topic.author.id != current_user.id:
        make_notification(
            topic.author.id,
            f"{current_user.nickname} поставил(а) реакцию на ваш пост.",
            url_for("topic_view", topic_id=topic.id),
        )

    db.session.commit()
    return redirect(request.referrer or url_for("topic_view", topic_id=topic.id))


@app.route("/topic/<int:topic_id>/comment", methods=["POST"])
@login_required
def create_comment(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    content = request.form.get("content", "").strip()

    if not content:
        flash("Комментарий не может быть пустым.")
        return redirect(url_for("topic_view", topic_id=topic.id))

    comment = Comment(content=content, topic_id=topic.id, user_id=current_user.id)
    db.session.add(comment)

    if topic.author.id != current_user.id:
        make_notification(
            topic.author.id,
            f"{current_user.nickname} оставил(а) комментарий под вашим постом.",
            url_for("topic_view", topic_id=topic.id) + "#comments",
        )

    db.session.commit()
    flash("Комментарий добавлен.")
    return redirect(url_for("topic_view", topic_id=topic.id) + "#comments")


@app.route("/comment/<int:comment_id>/reply", methods=["POST"])
@login_required
def reply_comment(comment_id):
    parent = Comment.query.get_or_404(comment_id)
    content = request.form.get("content", "").strip()

    if not content:
        flash("Ответ не может быть пустым.")
        return redirect(url_for("topic_view", topic_id=parent.topic_id) + "#comments")

    reply = Comment(
        content=content,
        topic_id=parent.topic_id,
        user_id=current_user.id,
        parent_id=parent.id,
    )
    db.session.add(reply)

    if parent.author.id != current_user.id:
        make_notification(
            parent.author.id,
            f"{current_user.nickname} ответил(а) на ваш комментарий.",
            url_for("topic_view", topic_id=parent.topic_id) + "#comments",
        )

    db.session.commit()
    flash("Ответ добавлен.")
    return redirect(url_for("topic_view", topic_id=parent.topic_id) + "#comments")


@app.route("/comment/<int:comment_id>/react", methods=["POST"])
@login_required
def react_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    reaction = request.form.get("reaction", "").strip()

    if reaction not in REACTIONS:
        abort(400)

    existing = CommentReaction.query.filter_by(comment_id=comment.id, user_id=current_user.id).first()
    if existing:
        if existing.reaction == reaction:
            db.session.delete(existing)
            flash("Реакция убрана.")
        else:
            existing.reaction = reaction
            flash("Реакция изменена.")
    else:
        db.session.add(CommentReaction(comment_id=comment.id, user_id=current_user.id, reaction=reaction))
        flash("Реакция добавлена.")

    if comment.author.id != current_user.id:
        make_notification(
            comment.author.id,
            f"{current_user.nickname} отреагировал(а) на ваш комментарий.",
            url_for("topic_view", topic_id=comment.topic_id) + "#comments",
        )

    db.session.commit()
    return redirect(request.referrer or url_for("topic_view", topic_id=comment.topic_id) + "#comments")


@app.route("/topic/<int:topic_id>/edit", methods=["GET", "POST"])
@login_required
def edit_topic(topic_id):
    topic = Topic.query.get_or_404(topic_id)

    if not can_manage_topic(topic):
        flash("У вас нет доступа к этому посту.")
        return redirect(url_for("topic_view", topic_id=topic.id))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()

        if not title or not content:
            flash("Заполни заголовок и текст.")
            return render_template("edit_topic.html", topic=topic)

        new_image = request.files.get("image")
        new_image_name = save_uploaded_image(new_image)
        if new_image_name is None:
            flash("Фото должно быть изображением.")
            return render_template("edit_topic.html", topic=topic)

        if new_image_name:
            delete_uploaded_image(topic.image)
            topic.image = new_image_name

        topic.title = title
        topic.content = content
        db.session.commit()
        flash("Пост обновлён.")
        return redirect(url_for("topic_view", topic_id=topic.id))

    return render_template("edit_topic.html", topic=topic)


@app.route("/topic/<int:topic_id>/delete", methods=["POST"])
@login_required
def delete_topic(topic_id):
    topic = Topic.query.get_or_404(topic_id)

    if not can_manage_topic(topic):
        flash("У вас нет доступа к этому посту.")
        return redirect(url_for("forum"))

    for root_comment in Comment.query.filter_by(topic_id=topic.id, parent_id=None).all():
        delete_comment_tree(root_comment)

    Reaction.query.filter_by(topic_id=topic.id).delete(synchronize_session=False)
    delete_uploaded_image(topic.image)

    db.session.delete(topic)
    db.session.commit()
    flash("Пост удалён.")
    return redirect(url_for("forum"))


@app.route("/comment/<int:comment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)

    if not can_manage_comment(comment):
        flash("У вас нет доступа к этому комментарию.")
        return redirect(url_for("topic_view", topic_id=comment.topic_id))

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if not content:
            flash("Комментарий не может быть пустым.")
            return render_template("edit_comment.html", comment=comment)

        comment.content = content
        db.session.commit()
        flash("Комментарий обновлён.")
        return redirect(url_for("topic_view", topic_id=comment.topic_id) + "#comments")

    return render_template("edit_comment.html", comment=comment)


@app.route("/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)

    if not can_manage_comment(comment):
        flash("У вас нет доступа к этому комментарию.")
        return redirect(url_for("topic_view", topic_id=comment.topic_id))

    delete_comment_tree(comment)
    db.session.commit()
    flash("Комментарий удалён.")
    return redirect(url_for("topic_view", topic_id=comment.topic_id) + "#comments")


@app.route("/profile/<username>")
@login_required
def profile(username):
    user = User.query.filter_by(username=username.strip().lower()).first_or_404()

    topics = Topic.query.filter_by(user_id=user.id).order_by(Topic.id.desc()).all()
    for topic in topics:
        decorate_topic(topic)

    followers_count = Follow.query.filter_by(following_id=user.id).count()
    following_count = Follow.query.filter_by(follower_id=user.id).count()
    posts_count = Topic.query.filter_by(user_id=user.id).count()
    is_following = False

    if current_user.id != user.id:
        is_following = Follow.query.filter_by(follower_id=current_user.id, following_id=user.id).first() is not None

    return render_template(
        "profile.html",
        user=user,
        topics=topics,
        followers_count=followers_count,
        following_count=following_count,
        posts_count=posts_count,
        is_following=is_following,
    )


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        bio = request.form.get("bio", "").strip()

        if not nickname:
            flash("Ник не может быть пустым.")
            return render_template("edit_profile.html")

        avatar_file = request.files.get("avatar")
        avatar_name = save_uploaded_image(avatar_file)
        if avatar_name is None:
            flash("Аватарка должна быть изображением.")
            return render_template("edit_profile.html")

        if avatar_name:
            delete_uploaded_image(current_user.avatar)
            current_user.avatar = avatar_name

        current_user.nickname = nickname
        current_user.bio = bio
        db.session.commit()
        flash("Профиль обновлён.")
        return redirect(url_for("profile", username=current_user.username))

    return render_template("edit_profile.html")


@app.route("/admin/profile/<username>", methods=["GET", "POST"])
@login_required
def admin_edit_profile(username):
    if not current_user.is_admin:
        flash("Только админ может редактировать чужие профили.")
        return redirect(url_for("forum"))

    user = User.query.filter_by(username=username.strip().lower()).first_or_404()

    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        bio = request.form.get("bio", "").strip()
        status = request.form.get("status", "").strip()

        if not nickname:
            flash("Ник не может быть пустым.")
            return render_template("admin_edit_profile.html", user=user)

        avatar_file = request.files.get("avatar")
        avatar_name = save_uploaded_image(avatar_file)
        if avatar_name is None:
            flash("Аватарка должна быть изображением.")
            return render_template("admin_edit_profile.html", user=user)

        if avatar_name:
            delete_uploaded_image(user.avatar)
            user.avatar = avatar_name

        user.nickname = nickname
        user.bio = bio
        user.status = status
        db.session.commit()
        flash("Профиль пользователя обновлён.")
        return redirect(url_for("profile", username=user.username))

    return render_template("admin_edit_profile.html", user=user)


@app.route("/follow/<username>", methods=["POST"])
@login_required
def follow_user(username):
    user = User.query.filter_by(username=username.strip().lower()).first_or_404()

    if user.id == current_user.id:
        flash("Нельзя подписаться на себя.")
        return redirect(url_for("profile", username=user.username))

    relation = Follow.query.filter_by(follower_id=current_user.id, following_id=user.id).first()

    if relation:
        db.session.delete(relation)
        flash("Вы отписались.")
    else:
        db.session.add(Follow(follower_id=current_user.id, following_id=user.id))
        make_notification(
            user.id,
            f"{current_user.nickname} подписался(ась) на вас.",
            url_for("profile", username=current_user.username),
        )
        flash("Вы подписались.")

    db.session.commit()
    return redirect(url_for("profile", username=user.username))


@app.route("/ban/<username>", methods=["POST"])
@login_required
def ban_user(username):
    if not current_user.is_admin:
        flash("Только админ может банить пользователей.")
        return redirect(request.referrer or url_for("forum"))

    user = User.query.filter_by(username=username.strip().lower()).first_or_404()

    if user.id == current_user.id:
        flash("Нельзя забанить себя.")
        return redirect(request.referrer or url_for("forum"))

    user.is_banned = True
    db.session.commit()
    flash(f"Пользователь {user.username} заблокирован.")
    return redirect(request.referrer or url_for("forum"))


@app.route("/notifications")
@login_required
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).all()
    return render_template("notifications.html", notifications=items)


@app.route("/notifications/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    note = Notification.query.get_or_404(notification_id)
    if note.user_id != current_user.id:
        abort(403)

    note.is_read = True
    db.session.commit()
    return redirect(note.link or url_for("notifications"))


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)