from functools import wraps
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .documentos import normalizar_documento
from .fornecedor_forms import (
    FornecedorForm,
    InativacaoFornecedorForm,
    adicionar_erros,
)
from .fornecedores import CAMPOS
from .models import Fornecedor
from .services import (
    cadastrar_fornecedor,
    editar_fornecedor,
    inativar_fornecedor,
)


def gerente_fornecedores(view):
    @wraps(view)
    @login_required(login_url="login")
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_superuser or request.user.cargo == "G"):
            return HttpResponseForbidden(
                "Apenas o gerente pode administrar fornecedores."
            )
        return view(request, *args, **kwargs)

    return wrapped


@login_required(login_url="login")
@require_GET
def listar(request):
    termo = request.GET.get("q", "").strip()[:160]
    situacao = request.GET.get("situacao", "ativos")
    fornecedores = Fornecedor.objects.all()
    if situacao == "inativos":
        fornecedores = fornecedores.filter(ativo=False)
    elif situacao != "todos":
        situacao = "ativos"
        fornecedores = fornecedores.filter(ativo=True)
    if termo:
        documento = normalizar_documento(termo)
        consulta = Q(nome__icontains=termo) | Q(nome_fantasia__icontains=termo)
        consulta |= Q(contato__icontains=termo) | Q(email__icontains=termo)
        if documento:
            consulta |= Q(documento__icontains=documento)
        fornecedores = fornecedores.filter(consulta)
    pagina = Paginator(fornecedores, 20).get_page(request.GET.get("page"))
    filtros = request.GET.copy()
    filtros.pop("page", None)
    return render(
        request,
        "fornecedores/lista.html",
        {
            "pagina": pagina,
            "q": termo,
            "situacao": situacao,
            "filtros": filtros.urlencode(),
        },
    )


@gerente_fornecedores
@require_http_methods(["GET", "POST"])
def formulario(request, pk=None):
    fornecedor = get_object_or_404(Fornecedor, pk=pk) if pk is not None else None
    initial = (
        {campo: getattr(fornecedor, campo) for campo in CAMPOS} if fornecedor else {}
    )
    initial["chave_idempotencia"] = uuid4().hex
    form = FornecedorForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
        editing=fornecedor is not None,
    )
    if request.method == "POST" and form.is_valid():
        kwargs = {
            "actor": request.user,
            "dados": {campo: form.cleaned_data[campo] for campo in CAMPOS},
            "idempotency_key": form.cleaned_data["chave_idempotencia"],
        }
        try:
            if fornecedor:
                resultado = editar_fornecedor(
                    **kwargs,
                    fornecedor_id=fornecedor.pk,
                    reason=form.cleaned_data["motivo"],
                )
            else:
                resultado = cadastrar_fornecedor(**kwargs)
        except ValidationError as error:
            adicionar_erros(form, error)
        else:
            messages.success(request, "Fornecedor salvo com sucesso.")
            return redirect("fornecedor_detalhe", pk=resultado.pk)
    return render(
        request,
        "fornecedores/formulario.html",
        {
            "form": form,
            "fornecedor": fornecedor,
        },
    )


@login_required(login_url="login")
@require_GET
def detalhe(request, pk):
    fornecedor = get_object_or_404(Fornecedor, pk=pk)
    entradas = (
        fornecedor.entradas.select_related("responsavel")
        .prefetch_related(
            "itens__produto",
        )
        .order_by("-criada_em", "-pk")
    )
    return render(
        request,
        "fornecedores/detalhe.html",
        {
            "fornecedor": fornecedor,
            "pagina": Paginator(entradas, 20).get_page(request.GET.get("page")),
            "ultima_alteracao": fornecedor.alteracoes.select_related(
                "responsavel"
            ).first(),
            "inativacao": fornecedor.inativacoes.select_related("responsavel").first(),
        },
    )


@gerente_fornecedores
@require_http_methods(["GET", "POST"])
def inativar(request, pk):
    fornecedor = get_object_or_404(Fornecedor, pk=pk)
    form = InativacaoFornecedorForm(
        request.POST if request.method == "POST" else None,
        initial={"chave_idempotencia": uuid4().hex},
    )
    if request.method == "POST" and form.is_valid():
        try:
            inativar_fornecedor(
                actor=request.user,
                fornecedor_id=pk,
                reason=form.cleaned_data["motivo"],
                idempotency_key=form.cleaned_data["chave_idempotencia"],
            )
        except ValidationError as error:
            adicionar_erros(form, error)
        else:
            messages.success(
                request, "Fornecedor inativado. O histórico foi preservado."
            )
            return redirect("fornecedor_detalhe", pk=pk)
    return render(
        request,
        "fornecedores/inativar.html",
        {
            "fornecedor": fornecedor,
            "form": form,
        },
    )
