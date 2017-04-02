var dict = {
  class : {
    "NEUTRAL" : "neutral",
    "WARRIOR" : "warrior",
    "ROGUE" : "rogue",
    "MAGE" : "mage",
    "PALADIN" : "paladin",
    "PRIEST" : "priest",
    "DRUID" : "druid",
    "WARLOCK" : "warlock",
    "SHAMAN" : "shaman",
    "HUNTER" : "hunter"
  },

  classText: {
    "NEUTRAL" : "Neutral",
    "WARRIOR" : "Warrior",
    "ROGUE" : "Rogue",
    "MAGE" : "Mage",
    "PALADIN" : "Paladin",
    "PRIEST" : "Priest",
    "DRUID" : "Druid",
    "WARLOCK" : "Warlock",
    "SHAMAN" : "Shaman",
    "HUNTER" : "Hunter"
  },

  type : {
    "MINION" : "Minion",
    "SPELL" : "Spell",
    "WEAPON" : "Weapon"
  },

  rarity : {
    "LEGENDARY" : "Legendary",
    "EPIC" : "Epic",
    "RARE" : "Rare",
    "COMMON" : "Common",
    "FREE" : "Free"
  },

  set : {
    "BRM": "Blackrock",
    "EXPERT1" : "Expert",
    "OG" : "Old Gods",
    "TGT" : "Grand Tourney",
    "GANGS" : "Gadgetzan",
    "KARA" : "Karazhan",
    "CORE" : "Core",
    "LOE" : "Explorers",
    "GVG" : "Goblins/Gnomes"

  }
}
;var filters = {

    filterStates : {},

    filterStatesDefault : {
        "class" : {
            "NEUTRAL" : true,
            "WARRIOR" : true,
            "ROGUE" : true,
            "MAGE" : true,
            "PALADIN" : true,
            "PRIEST" : true,
            "DRUID" : true,
            "WARLOCK" : true,
            "SHAMAN" : true,
            "HUNTER" : true
        },

        "type" : {
            "MINION" : true,
            "WEAPON" : true,
            "SPELL" : true
        }

    },

    filteredList : [],

    resetFilter : function(){
        filters.filterStates = jQuery.extend(true, {}, filters.filterStatesDefault);
    },

    handle : function(div){
        params = div.split("-");

        params[2] = params[2].toUpperCase();

        filters.filterStates[params[1]][params[2]] = !filters.filterStates[params[1]][params[2]];

        if (filters.filterStates[params[1]][params[2]] == true){
            $("#" + div).removeClass("not-selected");
            $("#" + div).addClass("selected");
        } else {
            $("#" + div).removeClass("selected");
            $("#" + div).addClass("not-selected");
        }


        filters.filter();
    },

    filter : function(){
        filters.filteredList = [];
        sort.resetSort();


        $.each(cardData, function(card, data){

            data.filtered = true;
            //class filters
            if (filters.filterStates.class[data.cardClass] != true ){
                data.filtered = false;
            }

            else if (filters.filterStates.type[data.type] != true ){
                data.filtered = false;
            }


            if (data.filtered == true){
                filters.filteredList.push(data);
            }


        })

        sort.sortData("name", "asc");
        filters.writeTable();
    },

    writeTable : function(){

      $("#display-cardstotal").text(filters.filteredList.length)

      $("#display-table-data").empty();

      $.each(filters.filteredList, function(i, data){
              var divider = "</div><div>";
              var toAdd = "<div class='" + dict.class[data.cardClass] + "'><div>";

              toAdd += data.name + divider + dict.classText[data.cardClass] + divider + dict.type[data.type]
                        + divider + dict.rarity[data.rarity] + divider + dict.set[data.set] +
                        divider + data.cost + "</div></div>";
              $("#display-table-data").append(toAdd);

      })
    }
}
;function getData(){

	$.when(
		$.getJSON("cards_simple.json", function(data){

			cardData = data;
		})
	).then(function(){
		pageLoad();
	})
};

function pageLoad(){
	$.each(cardData, function(card, data){
		data["filtered"] = true;
	})

	// run filter and write table
	filters.resetFilter();
	filters.filter();


}

var cardData = [];

function initialization(){

	$(document).ready(function(){
		getData();
	});
}

initialization();
;var sort = {

  sortStates : {},

  sortStatesDefault : {
    "name" : "asc",
    "cardClass" : "desc",
    "type" : "desc" ,
    "rarity" : "desc",
    "set" : "desc",
    "cost" : "desc"
  },

  resetSort : function(){
    sort.sortStates = jQuery.extend(true, {}, sort.sortStatesDefault);
  },

  sortTable : function(parameter){
    sort.changeHeader(parameter);
    parameter = parameter.slice(8)

    if (sort.sortStates[parameter] == "asc") {
      sort.sortStates[parameter] = "desc";
      sort.sortData(parameter, "desc");
    } else if (sort.sortStates[parameter] == "desc") {
      sort.sortStates[parameter] = "asc";
      sort.sortData(parameter, "asc");
    }

    filters.writeTable();

  },

  changeHeader : function(parameter){
    $(".sort-active").removeClass();
    $("#" + parameter).addClass("sort-active")
  },

  sortData : function(parameter, sorttype){

    var sortArray = filters.filteredList.map(function(data, ind){
      return {ind:ind, data:data}
    });

    // sort works both alphabetically and numerically
    if (sorttype == "asc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return -1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return 1;
        }
        return a.ind - b.ind;
      })
    }
    if (sorttype == "desc"){
      sortArray.sort(function(a, b){
        if (a.data[parameter] < b.data[parameter]){
          return 1;
        }
        if (a.data[parameter] > b.data[parameter]){
          return -1;
        }
        return a.ind - b.ind;
      })
    }


    filters.filteredList = sortArray.map(function(val){
      return val.data;
    });

  }
}
