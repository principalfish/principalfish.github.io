var uiAttr = {
  changeNavBar : function(elem){
    $(".navbaractive").removeClass("navbaractive");

    var id = "#nav-" + elem;

    $(id).addClass("navbaractive");
      // change pagetitle;


    var text = $(id).text();

    $("#pagetitle").html(text);

    document.title = text;

  },

  clickMapButton : function(div){
    var divClass = $(div).attr("class");
    var divID = $(div).attr("id");

    if (divClass.indexOf("mapbuttonactive") == -1){

      $(div).addClass("mapbuttonactive");
      uiAttr.showDiv(divID);
    } else {
      uiAttr.hideDiv(divID);
    }
  },

  showDiv: function(id){

    // check z indexes
    uiAttr.zIndexCheck(id)

    if (id == "battlegrounds"){
      battleground.active = true;
    }
     if (id == "redistribute"){
       redistribute.active = true;
     }
    // make div visible and draggable by h2
    // on mousedown change z-index - bring to front
    $(uiAttr.buttonToDiv[id]).show();
    $(uiAttr.buttonToDiv[id]).mousedown(function(){
      uiAttr.zIndexCheck(id);
      uiAttr.zIndexShuffle();
    }).draggable({
      handle: "h2",
      containment: "#wrapper"
    });

    //alter z index
    uiAttr.zIndexShuffle();
  },

  hideDiv: function(id){

    var i = uiAttr.zIndexTracker.indexOf(id);
    $("#" + id).removeClass("mapbuttonactive");

    if (id == "battlegrounds"){
      battleground.active = false;
    }

    if (id == "redistribute"){
      redistribute.active = false;
    }

    if (i != -1){
      uiAttr.zIndexTracker.splice(i, 1);
    }

    $(uiAttr.buttonToDiv[id]).hide();
    uiAttr.zIndexShuffle();
  },

  // map buttons to divs
  buttonToDiv : {
    "seat-information" : "#seat-information",
    "votetotalsbutton" : "#votetotals",
    "filtersbutton" : "#filters",
    "choroplethsbutton" : "#choropleths",
    "seatlistbutton" : "#seatlist",
    "seatlist-extend" : "#seatlist-extended",
    "predictbutton" : "#userinput",
    "seat-600" : "#seat-600",
    "battlegrounds" : "#battlegroundsselect",
    "redistribute" : "#redistributeselect"
  },

  //store  and reorder z indexes of hidden divs
  zIndexTracker : [],

  zIndexCheck : function(id){
    if (uiAttr.zIndexTracker.indexOf(id) == -1 ){
      uiAttr.zIndexTracker.push(id);

    } else {
      var fromIndex = uiAttr.zIndexTracker.indexOf(id);
      var toIndex = uiAttr.zIndexTracker.length - 1;

      uiAttr.zIndexTracker.splice(fromIndex, 1);
      uiAttr.zIndexTracker.splice(toIndex, 0, id);
    }
  },

  zIndexShuffle: function(){

    var zIndices =  uiAttr.zIndexTracker;

    for (var i = 0; i < zIndices.length; i++){
      $(uiAttr.buttonToDiv[zIndices[i]]).css("z-index", i + 1);
    }
  },

  pageLoadDiv : function(param){
    $.each(uiAttr.buttonToDiv, function(button, div){
      if (button == "votetotalsbutton" || button == "filtersbutton" || button == "choroplethsbutton" || button == "seatlistbutton"){
        uiAttr.showDiv(button);
      } else if (button == "predictbutton" && currentMap.predict == true){
        $("#predictbutton").removeClass("hidden").addClass("mapbuttonactive");
        uiAttr.showDiv(button);
      } else if (battleground.active){
        uiAttr.showDiv("battlegroundsbutton")
      } else if (redistribute.active){
        uiAttr.showDiv("redistributebutton")

      }
      else {
        uiAttr.hideDiv(button);
      }
    });

  }
}
